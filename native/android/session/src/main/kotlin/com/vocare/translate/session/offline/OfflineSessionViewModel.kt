package com.vocare.translate.session.offline

import androidx.lifecycle.ViewModel
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.model.LiveState
import com.vocare.translate.core.model.SessionConfig
import com.vocare.translate.core.model.SessionPhase
import com.vocare.translate.core.model.Side
import com.vocare.translate.core.model.SideState
import com.vocare.translate.core.model.SpeechLocales
import com.vocare.translate.core.model.Turn
import com.vocare.translate.core.model.TurnView
import com.vocare.translate.core.offline.OfflineEngine
import com.vocare.translate.core.offline.PairStatus
import com.vocare.translate.core.offline.Recognition
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

/**
 * Hold → on-device recognition, release → on-device translation → on-device
 * synthesis (CONTRACT §4, JSX `OfflineLiveScreen`). Never touches the network
 * and, like the JSX, does not write history.
 */
class OfflineSessionViewModel(
    private val config: SessionConfig,
    private val engine: OfflineEngine,
    dispatcher: CoroutineDispatcher = Dispatchers.Main.immediate,
) : ViewModel() {

    private enum class Phase { IDLE, LISTENING, TRANSLATING, SPEAKING }

    /** One hold/release operation. Only the current one may touch state (JSX `operationRef`). */
    private class Operation(val side: Side) {
        var released = false
        var recognition: Recognition? = null
        val started = Job()
    }

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val turns = mutableListOf<Turn>()
    private var holding: Side? = null
    private var phase = Phase.IDLE
    private var sessionPhase: SessionPhase = SessionPhase.Live
    private var notice: String? = null
    private var partial = ""
    private var elapsed = 0
    private var operation: Operation? = null
    private var releaseJob: Job? = null
    private val timer: Job = scope.launch {
        while (isActive) {
            delay(1_000)
            elapsed += 1
            publish()
        }
    }

    private val _state = MutableStateFlow(render())
    val state: StateFlow<LiveState> = _state.asStateFlow()

    private val _endReason = MutableStateFlow<EndReason?>(null)
    val endReason: StateFlow<EndReason?> = _endReason.asStateFlow()

    val transcript: List<Turn> get() = turns.toList()

    fun hold(side: Side) {
        if (operation != null || phase != Phase.IDLE || sessionPhase is SessionPhase.Ended) return
        val op = Operation(side)
        operation = op
        notice = null
        partial = ""
        holding = side
        phase = Phase.LISTENING
        publish()

        val from = lang(side)
        scope.launch {
            try {
                when (engine.pairStatus(config.langA, config.langB)) {
                    PairStatus.INSTALLED -> Unit
                    // Installable in-app; a declined download is not an error, just no turn.
                    PairStatus.SUPPORTED -> if (!engine.prepare(config.langA, config.langB)) {
                        throw OfflineSpeechException("Download both translation languages before starting an offline session.")
                    }
                    PairStatus.UNSUPPORTED -> throw OfflineSpeechException(
                        "${Language.of(config.langA).label} and ${Language.of(config.langB).label} cannot be translated on this device.",
                    )
                }
                if (operation !== op) return@launch
                val recognition = engine.startRecognition(SpeechLocales.recognition(from)) { text ->
                    if (operation === op && holding == side) {
                        partial = text
                        publish()
                    }
                }
                if (operation !== op) {
                    recognition.cancel()
                    return@launch
                }
                op.recognition = recognition
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (operation === op) {
                    operation = null
                    holding = null
                    phase = Phase.IDLE
                    notice = e.message ?: "Could not start on-device speech recognition."
                    publish()
                }
            } finally {
                op.started.complete()
            }
        }
    }

    fun release(side: Side) {
        val op = operation ?: return
        if (op.side != side || op.released) return
        op.released = true
        holding = null
        phase = Phase.TRANSLATING
        publish()
        releaseJob = scope.launch { finishTurn(op) }
    }

    private suspend fun finishTurn(op: Operation) {
        val from = lang(op.side)
        val to = lang(op.side.other)

        op.started.join()
        if (operation !== op) return
        // Start failed: hold() already reported it and reset the phase.
        val recognition = op.recognition ?: return

        val heard = try {
            recognition.stop().trim()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            settle(op, e.message ?: "Could not finish speech recognition.")
            return
        }
        if (operation !== op) return
        if (heard.isEmpty()) {
            settle(
                op,
                "Nothing was recognised in ${Language.of(from).label}. " +
                    "On-device recognition for ${Language.of(from).label} may not be installed — " +
                    "download the recognition language in your device speech settings.",
            )
            return
        }

        val translated = try {
            engine.translate(heard, from, to).trim()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            ""
        }
        if (operation !== op) return
        if (translated.isEmpty()) {
            settle(
                op,
                "Could not translate ${Language.of(from).label} to ${Language.of(to).label} on this device. " +
                    "The language may not be downloaded — check Settings.",
            )
            return
        }

        turns += Turn(
            speaker = op.side.name,
            speakerName = name(op.side),
            originalLang = from,
            translatedLang = to,
            original = heard,
            translated = translated,
            side = op.side.name,
            from = from,
            to = to,
        )
        partial = ""
        phase = Phase.SPEAKING
        publish()
        try {
            engine.speak(translated, SpeechLocales.speech(to))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (operation === op) notice = e.message ?: "Could not speak the translation offline."
        }
        if (operation !== op) return
        operation = null
        phase = Phase.IDLE
        publish()
    }

    private fun settle(op: Operation, message: String) {
        if (operation !== op) return
        operation = null
        phase = Phase.IDLE
        partial = ""
        notice = message
        publish()
    }

    fun end() {
        if (sessionPhase is SessionPhase.Ended) return
        cancelOperation()
        timer.cancel()
        sessionPhase = SessionPhase.Ended
        _endReason.value = EndReason.USER
        publish()
    }

    private fun cancelOperation() {
        val op = operation ?: return
        operation = null
        releaseJob?.cancel()
        op.recognition?.let { recognition -> scope.launch { recognition.cancel() } }
        holding = null
        phase = Phase.IDLE
        partial = ""
    }

    override fun onCleared() {
        cancelOperation()
        scope.cancel()
    }

    // ── projection ───────────────────────────────────────────────────────────

    private fun publish() {
        _state.value = render()
    }

    private fun render(): LiveState = LiveState(
        sideA = renderSide(Side.A),
        sideB = renderSide(Side.B),
        elapsedSeconds = elapsed,
        phase = sessionPhase,
        badge = BADGE,
        notice = notice,
    )

    private fun renderSide(side: Side): SideState {
        val busy = phase != Phase.IDLE
        val mine = holding == side
        val turnViews = turns.mapIndexed { i, t -> TurnView(key = i, turn = t, mine = t.side == side.name) }
        return SideState(
            name = name(side),
            langCode = lang(side),
            langLabel = Language.of(lang(side)).label,
            pressing = mine,
            disabled = sessionPhase is SessionPhase.Ended || (busy && !mine),
            dimmed = busy && !mine,
            status = when {
                mine -> "Recording"
                phase == Phase.TRANSLATING -> "Translating..."
                phase == Phase.SPEAKING -> "Speaking..."
                holding != null -> "Listening to ${name(side.other)}"
                turns.isNotEmpty() -> "Ready - speak again"
                else -> "Ready"
            },
            turns = turnViews,
            // The partial belongs on the speaker's own half — it is what they are saying.
            note = if (mine && partial.isNotEmpty()) partial else null,
        )
    }

    private fun lang(side: Side) = if (side == Side.A) config.langA else config.langB
    private fun name(side: Side) = if (side == Side.A) config.nameA else config.nameB

    companion object {
        const val BADGE = "ON DEVICE"
    }
}
