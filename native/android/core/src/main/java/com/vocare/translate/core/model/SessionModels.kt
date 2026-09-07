package com.vocare.translate.core.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Which half of the phone. A is the phone's owner (bottom), B is across the table (top, rotated). */
enum class Side { A, B;
    val other: Side get() = if (this == A) B else A
}

/**
 * One persisted transcript entry. Cloud turns carry `speaker` (a pc_id) and
 * `original_lang`; offline turns carry `side`/`from`/`to`. The JSON keys match
 * what the Capacitor app already stored in Room, so history survives migration.
 */
@Serializable
data class Turn(
    val type: String = "turn",
    val speaker: String? = null,
    @SerialName("speaker_name") val speakerName: String? = null,
    @SerialName("original_lang") val originalLang: String? = null,
    @SerialName("translated_lang") val translatedLang: String? = null,
    val original: String = "",
    val translated: String? = null,
    val side: String? = null,
    val from: String? = null,
    val to: String? = null,
)

/** What the history store persists (CONTRACT §5 Persistence). */
data class SessionRecord(
    val sessionId: String,
    val callerName: String,
    val topic: String = "Translation Session",
    val languageA: String,
    val languageB: String,
    val participantA: String,
    val participantB: String,
    /** `active` while live, `ended` once torn down. */
    val status: String,
    val durationSeconds: Int,
    val transcript: List<Turn>,
)

/** Everything a live session needs to start. */
data class SessionConfig(
    val langA: String,
    val langB: String,
    val nameA: String = "Person A",
    val nameB: String = "Person B",
    val clientId: String,
    /** Seconds of cloud time allowed; `Double.POSITIVE_INFINITY` when unmetered. */
    val budgetSeconds: Double = Double.POSITIVE_INFINITY,
)

enum class SessionError { MIC, NETWORK }

sealed interface SessionPhase {
    data object Connecting : SessionPhase
    data object Live : SessionPhase
    data object Ended : SessionPhase
    data class Error(val error: SessionError) : SessionPhase
}

/** A turn as one panel sees it: `mine` = the reader's own words ("You said"), else "Translated". */
data class TurnView(val key: Int, val turn: Turn, val mine: Boolean)

data class SideState(
    val name: String,
    val langCode: String,
    val langLabel: String,
    val pressing: Boolean = false,
    val disabled: Boolean = true,
    val dimmed: Boolean = false,
    val status: String = "",
    val turns: List<TurnView> = emptyList(),
    /** Live partial transcript or a connecting note, shown with typing dots. */
    val note: String? = null,
)

data class LiveState(
    val sideA: SideState,
    val sideB: SideState,
    val elapsedSeconds: Int = 0,
    val phase: SessionPhase = SessionPhase.Connecting,
    /** Centre-bar badge, e.g. `ON DEVICE`; null shows the live dot instead. */
    val badge: String? = null,
    /** Red strip under the centre bar for recoverable errors (offline pipeline). */
    val notice: String? = null,
)
