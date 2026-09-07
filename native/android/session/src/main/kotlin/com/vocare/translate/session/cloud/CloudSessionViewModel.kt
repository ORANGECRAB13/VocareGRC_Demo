package com.vocare.translate.session.cloud

import androidx.lifecycle.ViewModel
import com.vocare.translate.core.api.ApiException
import com.vocare.translate.core.api.VocaApi
import com.vocare.translate.core.history.SessionHistoryStore
import com.vocare.translate.core.model.CreateSessionRequest
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.model.LiveState
import com.vocare.translate.core.model.OfferRequest
import com.vocare.translate.core.model.PollEvent
import com.vocare.translate.core.model.PttAction
import com.vocare.translate.core.model.SessionConfig
import com.vocare.translate.core.model.SessionError
import com.vocare.translate.core.model.SessionPhase
import com.vocare.translate.core.model.SessionRecord
import com.vocare.translate.core.model.Side
import com.vocare.translate.core.model.SideState
import com.vocare.translate.core.model.Turn
import com.vocare.translate.core.model.TurnView
import com.vocare.translate.session.EndReason
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** How the host asks for `RECORD_AUDIO`; the module cannot show the prompt itself. */
fun interface MicPermission {
    suspend fun request(): Boolean

    companion object {
        val Granted = MicPermission { true }
    }
}

/**
 * Face-to-face cloud session: two TURN-only legs, server-side push-to-talk
 * gates, a 1s poll loop and idempotent metering. Implements CONTRACT §3 step by
 * step and mirrors JSX `FaceToFaceLiveScreen`.
 *
 * Everything runs on [dispatcher] (the main thread in the app, a test dispatcher
 * in tests). Time is only ever consumed through `delay`, so tests drive the 1s
 * tick, the 15s consume watermark and the 36s turn fallback with virtual time.
 */
class CloudSessionViewModel(
    private val config: SessionConfig,
    private val api: VocaApi,
    private val history: SessionHistoryStore,
    private val legFactory: PeerLegFactory,
    private val micPermission: MicPermission = MicPermission.Granted,
    dispatcher: CoroutineDispatcher = Dispatchers.Main.immediate,
) : ViewModel() {

    private enum class TurnState { READY, RECORDING, TRANSLATING }

    private class SideRuntime {
        var turnState = TurnState.READY
        var pressing = false

        /** JSX pointer id: identifies the press whose hold gate is in flight. */
        var activePress: Int? = null
        var pressSerial = 0
        var leg: PeerLeg? = null
        var pcId: String? = null

        /** Fair FIFO — serialises hold/release per side (JSX `gateQueue`). */
        val gate = Mutex()
        var fallback: Job? = null
    }

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)

    /** Hangups and the final consume must outlive the session scope (JSX `fireAndForgetHangup`). */
    private val detachedScope = CoroutineScope(SupervisorJob() + dispatcher)

    private val sides = mapOf(Side.A to SideRuntime(), Side.B to SideRuntime())
    private val transcript = mutableListOf<Turn>()
    private var sessionId: String? = null
    private var elapsed = 0
    private var phase: SessionPhase = SessionPhase.Connecting
    private var connState = "Connecting…"
    private var timerJob: Job? = null
    private var pollJob: Job? = null
    private var setupJob: Job? = null
    private var tornDown = false

    private val _state = MutableStateFlow(render())
    val state: StateFlow<LiveState> = _state.asStateFlow()

    private val _endReason = MutableStateFlow<EndReason?>(null)

    /** Set together with `SessionPhase.Ended`; the host routes `EXHAUSTED` to the paywall. */
    val endReason: StateFlow<EndReason?> = _endReason.asStateFlow()

    val currentSessionId: String? get() = sessionId

    // ── §3.1–3.5 setup ───────────────────────────────────────────────────────

    fun start() {
        if (setupJob != null) return
        setupJob = scope.launch { setup() }
    }

    private suspend fun setup() {
        try {
            if (!micPermission.request()) {
                fail(SessionError.MIC, "Microphone access denied")
                return
            }
            val iceServers = api.iceServers()
            if (iceServers.isEmpty()) throw ApiException.NoIceServers()

            val id = api.createSession(
                CreateSessionRequest(
                    callerName = config.nameA,
                    callerLanguage = config.langA,
                    topic = TOPIC,
                    clientId = config.clientId,
                ),
            )
            sessionId = id
            persist(STATUS_ACTIVE)

            val legA = legFactory.create(iceServers) { onLegState(Side.A, it) }
            val legB = legFactory.create(iceServers) { onLegState(Side.B, it) }
            sides.getValue(Side.A).leg = legA
            sides.getValue(Side.B).leg = legB
            if (tornDown) return

            // Both clones are enabled while negotiating (see PeerLeg.createOffer).
            val offerA = legA.createOffer()
            val offerB = legB.createOffer()
            if (tornDown) return

            connectLeg(Side.A, id, offerA)
            if (tornDown) return
            connectLeg(Side.B, id, offerB)
            if (tornDown) return

            // RTP is established; push-to-talk owns the tracks from here on.
            legA.setMicEnabled(false)
            legB.setMicEnabled(false)
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException.PaymentRequired) {
            // 402 on /offer: metering refused the leg. Persist what exists and let the host
            // route to the `exhausted` paywall rather than a misleading network error.
            finish(EndReason.EXHAUSTED)
        } catch (e: Exception) {
            fail(SessionError.NETWORK, "Connection failed")
        }
    }

    private suspend fun connectLeg(side: Side, id: String, offer: LocalOffer) {
        val runtime = sides.getValue(side)
        val answer = api.offer(
            OfferRequest(
                sessionId = id,
                language = lang(side),
                name = name(side),
                sdp = offer.sdp,
                type = offer.type,
            ),
        )
        runtime.pcId = answer.pcId
        runtime.leg?.setAnswer(answer.sdp, answer.type)
    }

    /** §3.6: only leg A's state drives the screen, as in the JSX. Called from any thread. */
    private fun onLegState(side: Side, state: LegConnectionState) {
        if (side != Side.A) return
        scope.launch {
            if (tornDown || phase is SessionPhase.Ended) return@launch
            when (state) {
                LegConnectionState.CONNECTED -> {
                    if (phase is SessionPhase.Live) return@launch
                    phase = SessionPhase.Live
                    connState = "Connected"
                    startTimer()
                    startPoll()
                    publish()
                }
                LegConnectionState.FAILED, LegConnectionState.DISCONNECTED -> {
                    fail(SessionError.NETWORK, "Connection lost")
                }
                else -> Unit
            }
        }
    }

    // ── §3.7 push-to-talk ────────────────────────────────────────────────────

    fun hold(side: Side) {
        val me = sides.getValue(side)
        val other = sides.getValue(side.other)
        if (phase !is SessionPhase.Live) return
        if (me.turnState != TurnState.READY || other.pressing) return

        other.leg?.setMicEnabled(false)
        other.pressing = false
        me.pressing = true
        me.turnState = TurnState.RECORDING
        val token = ++me.pressSerial
        me.activePress = token
        publish()

        scope.launch {
            me.gate.withLock {
                try {
                    api.ptt(requirePcId(me), PttAction.HOLD)
                    // Only unmute once the server-side gate is open — and only if this
                    // press is still the live one (JSX pointerId check).
                    if (me.activePress == token) me.leg?.setMicEnabled(true)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    if (me.activePress == token) {
                        me.activePress = null
                        me.pressing = false
                        me.turnState = TurnState.READY
                        publish()
                    }
                }
            }
        }
    }

    fun release(side: Side) {
        val me = sides.getValue(side)
        if (me.activePress == null) return
        me.activePress = null
        me.leg?.setMicEnabled(false)
        me.pressing = false
        markTranslating(side)
        publish()

        scope.launch {
            me.gate.withLock {
                try {
                    val result = api.ptt(requirePcId(me), PttAction.RELEASE)
                    // Nothing captured → nothing will come back; don't wait for a turn.
                    if ((result.flushedBytes ?: 0L) <= 0L) markReady(side)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    markReady(side)
                }
            }
        }
    }

    private fun requirePcId(runtime: SideRuntime): String =
        runtime.pcId ?: throw IllegalStateException("Translation participant is not connected")

    private fun markTranslating(side: Side) {
        val me = sides.getValue(side)
        me.fallback?.cancel()
        me.turnState = TurnState.TRANSLATING
        // If the upstream translation never produces a turn, don't strand this speaker.
        me.fallback = scope.launch {
            delay(TURN_FALLBACK_MS)
            markReady(side)
        }
    }

    private fun markReady(side: Side) {
        val me = sides.getValue(side)
        me.fallback?.cancel()
        me.fallback = null
        if (tornDown) return
        me.turnState = TurnState.READY
        publish()
    }

    // ── §3.6/§3.8 poll ───────────────────────────────────────────────────────

    private fun startPoll() {
        if (pollJob != null) return
        pollJob = scope.launch {
            while (isActive) {
                delay(POLL_INTERVAL_MS)
                val id = sessionId ?: continue
                val response = try {
                    api.poll(id)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    continue
                }
                if (tornDown) return@launch
                var gotTurn = false
                for (event in response.events) {
                    when {
                        event.isTurn -> {
                            gotTurn = true
                            handleTurn(event)
                        }
                        event.isTurnFailed -> markReady(sideFor(event.speaker, event.originalLang))
                        else -> Unit // status, live and unknown types are ignored
                    }
                }
                if (gotTurn) persist(STATUS_ACTIVE)
                if (response.closed) {
                    finish(EndReason.SERVER_CLOSED)
                    return@launch
                }
            }
        }
    }

    /** §3.8 attribution: `speaker == pc_id_a`, falling back to `original_lang == langA`. */
    private fun sideFor(speaker: String?, originalLang: String?): Side {
        val pcIdA = sides.getValue(Side.A).pcId
        return if (speaker != null && pcIdA != null) {
            if (speaker == pcIdA) Side.A else Side.B
        } else {
            if (originalLang == config.langA) Side.A else Side.B
        }
    }

    private fun handleTurn(event: PollEvent) {
        transcript += event.toTurn()
        markReady(sideFor(event.speaker, event.originalLang))
    }

    // ── §3.9 timer + metering ────────────────────────────────────────────────

    private fun startTimer() {
        if (timerJob != null) return
        timerJob = scope.launch {
            while (isActive) {
                delay(TICK_MS)
                elapsed += 1
                if (elapsed % USAGE_REPORT_EVERY == 0) reportElapsed()
                if (elapsed >= config.budgetSeconds) {
                    // Out of credit: end rather than run on unbilled. Transcript is kept.
                    reportElapsed()
                    finish(EndReason.EXHAUSTED)
                    return@launch
                }
                publish()
            }
        }
    }

    /** Total elapsed seconds, never a delta — the server keeps a watermark. */
    private fun reportElapsed() {
        val id = sessionId ?: return
        val seconds = elapsed
        detachedScope.launch { api.consume(subject = config.clientId, sessionId = id, seconds = seconds) }
    }

    // ── §3.10 end / teardown ─────────────────────────────────────────────────

    /** User pressed End. */
    fun end() {
        if (phase is SessionPhase.Ended) return
        scope.launch {
            reportElapsed()
            finish(EndReason.USER)
        }
    }

    private suspend fun finish(reason: EndReason) {
        if (phase is SessionPhase.Ended) return
        persist(STATUS_ENDED)
        phase = SessionPhase.Ended
        _endReason.value = reason
        teardown()
        publish()
    }

    private fun fail(error: SessionError, message: String) {
        if (phase is SessionPhase.Ended) return
        connState = message
        phase = SessionPhase.Error(error)
        teardown()
        publish()
    }

    private fun teardown() {
        if (tornDown) return
        tornDown = true
        timerJob?.cancel(); timerJob = null
        pollJob?.cancel(); pollJob = null
        setupJob?.cancel()
        for ((_, runtime) in sides) {
            runtime.fallback?.cancel(); runtime.fallback = null
            runtime.activePress = null
            runtime.pressing = false
            runtime.turnState = TurnState.READY
            runtime.pcId?.let { pcId -> detachedScope.launch { api.hangup(pcId) } }
            runtime.pcId = null
            runtime.leg?.close()
            runtime.leg = null
        }
    }

    private suspend fun persist(status: String) {
        val id = sessionId ?: return
        history.save(
            SessionRecord(
                sessionId = id,
                callerName = config.nameA,
                topic = TOPIC,
                languageA = config.langA,
                languageB = config.langB,
                participantA = config.nameA,
                participantB = config.nameB,
                status = status,
                durationSeconds = elapsed,
                transcript = transcript.toList(),
            ),
        )
    }

    override fun onCleared() {
        if (phase !is SessionPhase.Ended && !tornDown) {
            // Leaving the screen without End: persist and hang up.
            detachedScope.launch { persist(STATUS_ENDED) }
            teardown()
        }
        scope.cancel()
    }

    // ── state projection (JSX render) ────────────────────────────────────────

    private fun publish() {
        _state.value = render()
    }

    private fun render(): LiveState = LiveState(
        sideA = renderSide(Side.A),
        sideB = renderSide(Side.B),
        elapsedSeconds = elapsed,
        phase = phase,
        badge = null,
    )

    private fun renderSide(side: Side): SideState {
        val me = sides.getValue(side)
        val other = sides.getValue(side.other)
        val live = phase is SessionPhase.Live
        val translating = me.turnState == TurnState.TRANSLATING
        val turns = transcript.mapIndexed { i, t ->
            TurnView(key = i, turn = t, mine = sideFor(t.speaker, t.originalLang) == side)
        }
        val hasSpoken = turns.any { it.mine }
        return SideState(
            name = name(side),
            langCode = lang(side),
            langLabel = Language.of(lang(side)).label,
            pressing = me.pressing,
            disabled = !live || translating || other.pressing,
            dimmed = translating,
            status = when {
                me.pressing -> "Recording"
                translating -> "Translating..."
                other.pressing -> "Listening to ${name(side.other)}"
                live -> if (hasSpoken) "Ready - speak again" else "Ready"
                else -> connState
            },
            turns = turns,
            note = if (phase is SessionPhase.Connecting && turns.isEmpty()) connState else null,
        )
    }

    private fun lang(side: Side) = if (side == Side.A) config.langA else config.langB
    private fun name(side: Side) = if (side == Side.A) config.nameA else config.nameB

    companion object {
        const val TOPIC = "Translation Session"
        const val STATUS_ACTIVE = "active"
        const val STATUS_ENDED = "ended"
        const val TICK_MS = 1_000L
        const val POLL_INTERVAL_MS = 1_000L
        const val USAGE_REPORT_EVERY = 15
        const val TURN_FALLBACK_MS = 36_000L
    }
}
