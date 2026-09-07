package com.vocare.translate.app.api

import com.vocare.translate.core.model.Turn
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Row of `GET /api/translation/sessions`. `duration` is a preformatted `MM:SS` string there. */
@Serializable
data class RemoteSessionSummary(
    @SerialName("session_id") val sessionId: String,
    @SerialName("caller_name") val callerName: String? = null,
    val lang: String? = null,
    val topic: String? = null,
    val status: String? = null,
    val duration: String? = null,
    @SerialName("participant_count") val participantCount: Int? = null,
)

@Serializable
data class RemoteSessionList(val sessions: List<RemoteSessionSummary> = emptyList())

/** `GET /api/translation/session/{id}`. Transcript entries are `turn` events. */
@Serializable
data class RemoteSessionDetail(
    @SerialName("session_id") val sessionId: String,
    @SerialName("caller_name") val callerName: String? = null,
    val lang: String? = null,
    val topic: String? = null,
    val status: String? = null,
    val transcript: List<Turn> = emptyList(),
)

/** What History and Session detail render, whichever store it came from. */
data class SessionSummary(
    val sessionId: String,
    val callerName: String,
    val topic: String,
    val lang: String,
    val status: String,
    val durationLabel: String?,
    val participantCount: Int?,
) {
    val isLive: Boolean get() = status == "live" || status == "active"
}

data class SessionDetail(
    val sessionId: String,
    val callerName: String,
    val topic: String,
    val transcript: List<Turn>,
)
