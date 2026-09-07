package com.vocare.translate.session.offline

import com.vocare.translate.core.model.SessionConfig
import com.vocare.translate.core.model.SessionPhase
import com.vocare.translate.core.model.Side
import com.vocare.translate.core.offline.PairStatus
import com.vocare.translate.session.EndReason
import com.vocare.translate.session.FakeOfflineEngine
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The on-device pipeline (CONTRACT §4) with a scripted engine: nothing here may
 * touch the network, and a declined language-pack download is not an error.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class OfflineSessionViewModelTest {

    private val config = SessionConfig(
        langA = "en",
        langB = "zh",
        nameA = "Alex",
        nameB = "Mei",
        clientId = "client-123",
    )

    private fun TestScope.model(engine: FakeOfflineEngine) =
        OfflineSessionViewModel(config, engine, StandardTestDispatcher(testScheduler))

    @Test
    fun `the badge says ON DEVICE from the first frame`() = runTest {
        val vm = model(FakeOfflineEngine())
        assertEquals("ON DEVICE", vm.state.value.badge)
        assertEquals(SessionPhase.Live, vm.state.value.phase)
        vm.end()
    }

    @Test
    fun `a full turn recognises, translates and speaks in the listener's locale`() = runTest {
        val engine = FakeOfflineEngine().apply {
            finalTranscript = "Hello, welcome to the clinic."
            translation = "你好，欢迎来到诊所。"
        }
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        assertEquals("Mandarin recognition answers to cmn-Hans-CN, not zh-CN", listOf("en-US"), engine.recognitionLocales)

        vm.release(Side.A)
        runCurrent()

        assertEquals(listOf(Triple("Hello, welcome to the clinic.", "en", "zh")), engine.translations)
        assertEquals(listOf("你好，欢迎来到诊所。" to "zh-CN"), engine.spoken)

        val state = vm.state.value
        assertEquals(1, state.sideA.turns.size)
        assertTrue("the speaker sees their own words", state.sideA.turns[0].mine)
        assertFalse("the listener sees a translation", state.sideB.turns[0].mine)
        assertEquals("你好，欢迎来到诊所。", state.sideB.turns[0].turn.translated)
        assertNull(state.notice)
        vm.end()
    }

    @Test
    fun `Mandarin uses the device recognition locale`() = runTest {
        val engine = FakeOfflineEngine()
        val vm = model(engine)
        vm.hold(Side.B)
        runCurrent()
        assertEquals(listOf("cmn-Hans-CN"), engine.recognitionLocales)
        vm.end()
    }

    @Test
    fun `partials land on the speaker's own panel only`() = runTest {
        val engine = FakeOfflineEngine()
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        engine.emitPartial("Hello, wel")
        runCurrent()

        assertEquals("Hello, wel", vm.state.value.sideA.note)
        assertNull("the listener never sees the speaker's partial", vm.state.value.sideB.note)
        assertTrue(vm.state.value.sideA.pressing)
        assertTrue("the other side is locked out mid-turn", vm.state.value.sideB.disabled)
        vm.end()
    }

    @Test
    fun `a supported pair is downloaded before the first turn`() = runTest {
        val engine = FakeOfflineEngine(status = PairStatus.SUPPORTED, prepareResult = true)
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        assertEquals(1, engine.prepareCalls)

        vm.release(Side.A)
        runCurrent()
        assertEquals(1, vm.state.value.sideA.turns.size)
        vm.end()
    }

    @Test
    fun `a declined download is a notice, not an error, and leaves the session usable`() = runTest {
        val engine = FakeOfflineEngine(status = PairStatus.SUPPORTED, prepareResult = false)
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()

        val state = vm.state.value
        assertEquals("the session stays live", SessionPhase.Live, state.phase)
        assertTrue("the user is told why nothing happened", state.notice!!.contains("Download"))
        assertFalse(state.sideA.pressing)
        assertFalse("and can try again", state.sideA.disabled)
        assertTrue(engine.translations.isEmpty())
        vm.end()
    }

    @Test
    fun `an unsupported pair names both languages and never starts recognition`() = runTest {
        val engine = FakeOfflineEngine(status = PairStatus.UNSUPPORTED)
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()

        val notice = vm.state.value.notice
        assertTrue("expected both language names in: $notice", notice!!.contains("English") && notice.contains("Mandarin"))
        assertTrue(engine.recognitionLocales.isEmpty())
        assertEquals(0, engine.prepareCalls)
        vm.end()
    }

    @Test
    fun `nothing recognised is reported without inventing a turn`() = runTest {
        val engine = FakeOfflineEngine().apply { finalTranscript = "   " }
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        vm.release(Side.A)
        runCurrent()

        assertTrue(vm.state.value.notice!!.contains("Nothing was recognised"))
        assertTrue(vm.state.value.sideA.turns.isEmpty())
        assertTrue(engine.translations.isEmpty())
        vm.end()
    }

    @Test
    fun `a translation failure is reported and no turn is recorded`() = runTest {
        val engine = FakeOfflineEngine().apply { translateThrows = true }
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        vm.release(Side.A)
        runCurrent()

        assertTrue(vm.state.value.notice!!.contains("Could not translate"))
        assertTrue(vm.state.value.sideA.turns.isEmpty())
        vm.end()
    }

    @Test
    fun `a missing local voice still keeps the transcript`() = runTest {
        val engine = FakeOfflineEngine().apply { speakThrows = true }
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        vm.release(Side.A)
        runCurrent()

        assertEquals(1, vm.state.value.sideA.turns.size)
        assertTrue(vm.state.value.notice!!.isNotBlank())
        vm.end()
    }

    @Test
    fun `end cancels the recognition in flight and marks the session ended`() = runTest {
        val engine = FakeOfflineEngine()
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        vm.end()
        runCurrent()

        assertEquals(SessionPhase.Ended, vm.state.value.phase)
        assertEquals(EndReason.USER, vm.endReason.value)
        assertTrue("the recogniser must be released", engine.cancelled)
        assertTrue(vm.state.value.sideA.disabled)
        assertTrue(vm.state.value.sideB.disabled)
    }

    @Test
    fun `a second hold while one is in flight is ignored`() = runTest {
        val engine = FakeOfflineEngine()
        val vm = model(engine)

        vm.hold(Side.A)
        runCurrent()
        vm.hold(Side.B)
        runCurrent()

        assertEquals("only one recogniser is ever started", 1, engine.recognitionLocales.size)
        assertTrue(vm.state.value.sideA.pressing)
        assertFalse(vm.state.value.sideB.pressing)
        vm.end()
    }

    @Test
    fun `the offline session never writes history`() = runTest {
        // There is no history store to inject: the offline view model's
        // constructor takes only the config and the engine (CONTRACT §6), which
        // is the guarantee that an on-device session cannot leak a transcript.
        val vm = model(FakeOfflineEngine())
        vm.hold(Side.A)
        runCurrent()
        vm.release(Side.A)
        runCurrent()
        assertEquals(1, vm.transcript.size)
        vm.end()
    }
}
