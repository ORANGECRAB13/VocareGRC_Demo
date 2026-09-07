package com.vocare.translate.session.cloud

import com.vocare.translate.core.model.PollEvent
import com.vocare.translate.core.model.PollResponse
import com.vocare.translate.core.model.PttAction
import com.vocare.translate.core.model.SessionConfig
import com.vocare.translate.core.model.SessionError
import com.vocare.translate.core.model.SessionPhase
import com.vocare.translate.core.model.Side
import com.vocare.translate.session.EndReason
import com.vocare.translate.session.FakeHistoryStore
import com.vocare.translate.session.FakeLegFactory
import com.vocare.translate.session.FakeVocaApi
import com.vocare.translate.session.Fixtures
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * CONTRACT §3, end to end, with a fake API and fake legs. Time is virtual:
 * every deadline in the protocol (the 1s poll, the 15s consume watermark, the
 * 36s turn fallback, the budget) is asserted by advancing the test clock, so
 * the suite runs in milliseconds and never flakes on a real one.
 *
 * The live session runs two never-ending loops, so these tests deliberately use
 * `advanceTimeBy` + `runCurrent` rather than `advanceUntilIdle`, which would
 * spin forever once the timer is running.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CloudSessionViewModelTest {

    private val config = SessionConfig(
        langA = "en",
        langB = "zh",
        nameA = "Alex",
        nameB = "Mei",
        clientId = "client-123",
    )

    private class Harness(
        val api: FakeVocaApi,
        val history: FakeHistoryStore,
        val legs: FakeLegFactory,
        val vm: CloudSessionViewModel,
        val scope: TestScope,
    ) {
        val state get() = vm.state.value

        fun tick(millis: Long) {
            scope.advanceTimeBy(millis)
            scope.runCurrent()
        }

        fun settle() = scope.runCurrent()
    }

    private val harnesses = mutableListOf<Harness>()

    /**
     * `runTest` drains the scheduler once the body returns, and a live session
     * runs two never-ending delay loops — so a test that left one running would
     * hang forever rather than fail. Every harness is torn down here.
     */
    private fun runSessionTest(body: suspend TestScope.() -> Unit) = runTest {
        try {
            body()
        } finally {
            harnesses.forEach { it.vm.end(); it.settle() }
            harnesses.clear()
        }
    }

    private fun TestScope.harness(
        config: SessionConfig,
        api: FakeVocaApi = FakeVocaApi(),
        micGranted: Boolean = true,
        connect: Boolean = true,
    ): Harness {
        val history = FakeHistoryStore()
        val legs = FakeLegFactory()
        val vm = CloudSessionViewModel(
            config = config,
            api = api,
            history = history,
            legFactory = legs,
            micPermission = MicPermission { micGranted },
            dispatcher = StandardTestDispatcher(testScheduler),
        )
        vm.start()
        // Safe before the session goes live: setup has no repeating timers.
        advanceUntilIdle()
        if (connect && legs.legs.isNotEmpty()) {
            legs.legA.report(LegConnectionState.CONNECTED)
            runCurrent()
        }
        return Harness(api, history, legs, vm, this).also { harnesses += it }
    }

    // ── setup (§3.1–3.6) ─────────────────────────────────────────────────────

    @Test
    fun `setup creates one session, two legs, and disables both mics once connected`() = runSessionTest {
        val h = harness(config)

        assertEquals(1, h.api.createdSessions)
        assertEquals(2, h.legs.legs.size)
        assertEquals(1, h.legs.legA.offersCreated)
        assertEquals(1, h.legs.legB.offersCreated)
        assertEquals("v=0 answer", h.legs.legA.answerSet)
        assertTrue("both clones are enabled while negotiating", h.legs.legA.micHistory.isNotEmpty())
        assertFalse("push-to-talk owns the tracks once connected", h.legs.legA.micOn)
        assertFalse(h.legs.legB.micOn)
        assertEquals(SessionPhase.Live, h.state.phase)
        assertEquals("active", h.history.saved.first().status)
    }

    @Test
    fun `a denied microphone goes to the mic error screen and never creates a session`() = runSessionTest {
        val h = harness(config, micGranted = false, connect = false)
        assertEquals(SessionPhase.Error(SessionError.MIC), h.state.phase)
        assertEquals(0, h.api.createdSessions)
    }

    @Test
    fun `an empty ICE list fails the session before any offer`() = runSessionTest {
        val h = harness(config, api = FakeVocaApi(ice = emptyList()), connect = false)
        assertEquals(SessionPhase.Error(SessionError.NETWORK), h.state.phase)
        assertEquals(0, h.api.createdSessions)
    }

    @Test
    fun `a 402 on offer ends the session as exhausted rather than as a network error`() = runSessionTest {
        val api = FakeVocaApi().apply { offerFailsWithPaymentRequired = true }
        val h = harness(config, api = api, connect = false)
        assertEquals(SessionPhase.Ended, h.state.phase)
        assertEquals(EndReason.EXHAUSTED, h.vm.endReason.value)
    }

    @Test
    fun `a failed leg routes to the network error screen`() = runSessionTest {
        val h = harness(config)
        h.legs.legA.report(LegConnectionState.FAILED)
        h.settle()
        assertEquals(SessionPhase.Error(SessionError.NETWORK), h.state.phase)
    }

    // ── push-to-talk (§3.7) ──────────────────────────────────────────────────

    @Test
    fun `hold mutes the other leg first and unmutes this one only after the server gate opens`() = runSessionTest {
        val h = harness(config)
        h.vm.hold(Side.A)

        assertTrue("the UI reacts immediately", h.state.sideA.pressing)
        assertFalse("the other leg is muted before the gate call", h.legs.legB.micOn)
        assertFalse("this leg stays muted until the gate answers", h.legs.legA.micOn)

        h.settle()
        assertEquals(listOf(FakeVocaApi.PC_A to PttAction.HOLD), h.api.pttCalls)
        assertTrue(h.legs.legA.micOn)
    }

    @Test
    fun `the other side is locked out while one side is pressing`() = runSessionTest {
        val h = harness(config)
        h.vm.hold(Side.A)
        h.settle()

        assertTrue("B's talk button is disabled", h.state.sideB.disabled)
        h.vm.hold(Side.B)
        h.settle()

        assertEquals("B's hold must never reach the server", 1, h.api.pttCalls.size)
        assertFalse(h.state.sideB.pressing)
    }

    @Test
    fun `a failed hold gate reverts the side to ready`() = runSessionTest {
        val api = FakeVocaApi().apply { pttThrows = true }
        val h = harness(config, api = api)
        h.vm.hold(Side.A)
        h.settle()

        assertFalse("the press is abandoned", h.state.sideA.pressing)
        assertFalse("and the mic was never opened", h.legs.legA.micOn)
        assertFalse("the side can press again", h.state.sideA.disabled)
    }

    @Test
    fun `release with no flushed bytes returns to ready without waiting for a turn`() = runSessionTest {
        val api = FakeVocaApi().apply { flushedBytesOnRelease = null }
        val h = harness(config, api = api)
        h.vm.hold(Side.A)
        h.settle()
        h.vm.release(Side.A)
        h.settle()

        assertEquals(listOf(FakeVocaApi.PC_A to PttAction.HOLD, FakeVocaApi.PC_A to PttAction.RELEASE), h.api.pttCalls)
        assertFalse(h.legs.legA.micOn)
        assertEquals("Ready", h.state.sideA.status)
    }

    @Test
    fun `release with flushed bytes waits, then gives up after the 36s fallback`() = runSessionTest {
        val h = harness(config)
        h.vm.hold(Side.A)
        h.settle()
        h.vm.release(Side.A)
        h.settle()

        assertEquals("Translating...", h.state.sideA.status)
        h.tick(35_000)
        assertEquals("still waiting one second short of the fallback", "Translating...", h.state.sideA.status)
        h.tick(1_500)
        assertEquals("Ready", h.state.sideA.status)
    }

    @Test
    fun `a turn for the speaker releases the wait before the fallback`() = runSessionTest {
        val api = FakeVocaApi(
            pollFrames = mutableListOf(
                PollResponse(
                    events = listOf(
                        PollEvent(type = "turn", speaker = FakeVocaApi.PC_A, originalLang = "en", original = "Hi", translated = "你好"),
                    ),
                ),
            ),
        )
        val h = harness(config, api = api)
        h.vm.hold(Side.A)
        h.settle()
        h.vm.release(Side.A)
        h.settle()
        assertEquals("Translating...", h.state.sideA.status)

        h.tick(1_100)
        assertEquals("Ready - speak again", h.state.sideA.status)
    }

    @Test
    fun `turn_failed puts that side back to ready`() = runSessionTest {
        val api = FakeVocaApi(
            pollFrames = mutableListOf(
                PollResponse(events = listOf(PollEvent(type = "turn_failed", speaker = FakeVocaApi.PC_B))),
            ),
        )
        val h = harness(config, api = api)
        h.vm.hold(Side.B)
        h.settle()
        h.vm.release(Side.B)
        h.settle()
        assertEquals("Translating...", h.state.sideB.status)

        h.tick(1_100)
        assertEquals("Ready", h.state.sideB.status)
        assertTrue("a failed turn leaves no transcript", h.state.sideB.turns.isEmpty())
    }

    // ── poll and attribution (§3.8) ──────────────────────────────────────────

    @Test
    fun `a turn spoken by A is Translated on B's panel and You said on A's`() = runSessionTest {
        val api = FakeVocaApi(
            pollFrames = mutableListOf(
                PollResponse(
                    events = listOf(
                        PollEvent(type = "turn", speaker = FakeVocaApi.PC_A, originalLang = "en", original = "Hello", translated = "你好"),
                        PollEvent(type = "turn", speaker = FakeVocaApi.PC_B, originalLang = "zh", original = "你好", translated = "Hello"),
                    ),
                ),
            ),
        )
        val h = harness(config, api = api)
        h.tick(1_100)

        assertEquals(2, h.state.sideA.turns.size)
        assertTrue("A spoke the first turn", h.state.sideA.turns[0].mine)
        assertFalse("so B reads it as a translation", h.state.sideB.turns[0].mine)
        assertFalse(h.state.sideA.turns[1].mine)
        assertTrue(h.state.sideB.turns[1].mine)
    }

    @Test
    fun `attribution survives teardown when the backend sends no original_lang`() = runSessionTest {
        // Regression: attribution used to read the live pc_id map, which teardown
        // clears — so every turn flipped to the other panel the moment the
        // session ended. With no `original_lang` the fallback cannot save it.
        val api = FakeVocaApi(
            pollFrames = mutableListOf(
                PollResponse(
                    events = listOf(
                        PollEvent(type = "turn", speaker = FakeVocaApi.PC_A, original = "Hello", translated = "你好"),
                    ),
                ),
                PollResponse(closed = true),
            ),
        )
        val h = harness(config, api = api)
        h.tick(1_100)
        assertTrue(h.state.sideA.turns[0].mine)

        h.tick(1_100)
        assertEquals(SessionPhase.Ended, h.state.phase)
        assertTrue("the turn still belongs to A after teardown", h.state.sideA.turns[0].mine)
        assertFalse(h.state.sideB.turns[0].mine)
    }

    @Test
    fun `the committed poll fixture drives the whole conversation onto the right panels`() = runSessionTest {
        val raw = Fixtures.read("poll_events.json")
        val json = Json { ignoreUnknownKeys = true }
        val root = json.parseToJsonElement(raw).jsonObject
        val frames = root.getValue("frames").jsonArray.map { json.decodeFromJsonElement(PollResponse.serializer(), it) }
        assertTrue("the fixture must actually have been read", frames.size > 5)

        val h = harness(config, api = FakeVocaApi(pollFrames = frames.toMutableList()))
        // One frame per second; the fixture closes on the last one.
        h.tick(frames.size * 1_000L + 500)

        val expected = root.getValue("expected").jsonObject
        val spokenByA = expected.getValue("turns_spoken_by_a").toString().toInt()
        val translatedOnB = expected.getValue("panel_b_translated_count").toString().toInt()

        assertEquals(spokenByA, h.state.sideA.turns.count { it.mine })
        assertEquals(translatedOnB, h.state.sideB.turns.count { !it.mine })
        assertEquals(SessionPhase.Ended, h.state.phase)
        assertEquals(EndReason.SERVER_CLOSED, h.vm.endReason.value)
    }

    // ── metering (§3.9) ──────────────────────────────────────────────────────

    @Test
    fun `elapsed seconds are reported every 15 seconds as a total, never a delta`() = runSessionTest {
        val h = harness(config)
        h.tick(14_500)
        assertTrue("nothing is reported before the watermark", h.api.consumes.isEmpty())

        h.tick(1_000)
        assertEquals(listOf(15), h.api.consumes)

        h.tick(15_000)
        assertEquals("the second report is the running total, not 15 again", listOf(15, 30), h.api.consumes)
    }

    @Test
    fun `reaching the budget reports, persists and ends the session as exhausted`() = runSessionTest {
        val h = harness(config.copy(budgetSeconds = 5.0))
        h.tick(5_500)

        assertEquals(SessionPhase.Ended, h.state.phase)
        assertEquals(EndReason.EXHAUSTED, h.vm.endReason.value)
        assertEquals("the final total is reported", listOf(5), h.api.consumes)
        assertEquals(1, h.history.endedCount)
        assertEquals(5, h.history.saved.last().durationSeconds)
    }

    @Test
    fun `an infinite budget never ends the session on its own`() = runSessionTest {
        val h = harness(config.copy(budgetSeconds = Double.POSITIVE_INFINITY))
        h.tick(120_000)
        assertEquals(SessionPhase.Live, h.state.phase)
    }

    // ── end and teardown (§3.10) ─────────────────────────────────────────────

    @Test
    fun `closed true ends the session and hangs up both legs`() = runSessionTest {
        val api = FakeVocaApi(pollFrames = mutableListOf(PollResponse(closed = true)))
        val h = harness(config, api = api)
        h.tick(1_100)

        assertEquals(SessionPhase.Ended, h.state.phase)
        assertEquals(EndReason.SERVER_CLOSED, h.vm.endReason.value)
        assertEquals(setOf(FakeVocaApi.PC_A, FakeVocaApi.PC_B), h.api.hangups.toSet())
        assertTrue(h.legs.legA.closed)
        assertTrue(h.legs.legB.closed)
    }

    @Test
    fun `end persists exactly one ended record however many times it is tapped`() = runSessionTest {
        // Regression: `finish` suspends on `persist` before flipping the phase,
        // so two End taps both got past the guard and wrote two `ended` rows.
        val h = harness(config)
        h.tick(3_000)
        h.vm.end()
        h.vm.end()
        h.vm.end()
        h.settle()

        assertEquals(SessionPhase.Ended, h.state.phase)
        assertEquals(EndReason.USER, h.vm.endReason.value)
        assertEquals(1, h.history.endedCount)
        assertEquals(2, h.api.hangups.size)
    }

    @Test
    fun `teardown stops the clock and the poll loop`() = runSessionTest {
        val h = harness(config)
        h.tick(3_000)
        h.vm.end()
        h.settle()

        val polls = h.api.pollCalls
        val elapsed = h.state.elapsedSeconds
        h.tick(10_000)
        assertEquals("no further polls after teardown", polls, h.api.pollCalls)
        assertEquals("the clock is frozen at the end", elapsed, h.state.elapsedSeconds)
    }

    @Test
    fun `the persisted record carries both languages, both names and the transcript`() = runSessionTest {
        val api = FakeVocaApi(
            pollFrames = mutableListOf(
                PollResponse(
                    events = listOf(
                        PollEvent(type = "turn", speaker = FakeVocaApi.PC_A, originalLang = "en", original = "Hello", translated = "你好"),
                    ),
                ),
            ),
        )
        val h = harness(config, api = api)
        h.tick(1_100)
        h.vm.end()
        h.settle()

        val record = h.history.saved.last()
        assertEquals("ended", record.status)
        assertEquals("en", record.languageA)
        assertEquals("zh", record.languageB)
        assertEquals("Alex", record.participantA)
        assertEquals("Mei", record.participantB)
        assertEquals(1, record.transcript.size)
        assertEquals("你好", record.transcript[0].translated)
    }
}
