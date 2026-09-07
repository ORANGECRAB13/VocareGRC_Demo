package com.vocare.translate.session

import com.vocare.translate.core.model.LiveState
import java.util.Locale

/**
 * Why a session reached `SessionPhase.Ended`. `:core`'s phase carries no reason,
 * so both view models expose this alongside their state; the host routes
 * [EXHAUSTED] to the paywall.
 */
enum class EndReason { USER, SERVER_CLOSED, EXHAUSTED }

/** JSX `formatClock`: mm:ss, zero padded, never negative. */
fun formatClock(seconds: Int): String {
    val n = maxOf(0, seconds)
    return String.format(Locale.ROOT, "%02d:%02d", n / 60, n % 60)
}

val LiveState.elapsedClock: String get() = formatClock(elapsedSeconds)
