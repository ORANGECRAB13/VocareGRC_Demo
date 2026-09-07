package com.vocare.translate.app.history

import android.content.Context
import androidx.room.Room
import com.vocare.translate.app.api.SessionDetail
import com.vocare.translate.app.api.SessionSummary
import com.vocare.translate.app.api.VocaAccountApi
import com.vocare.translate.core.history.SessionHistoryStore
import com.vocare.translate.core.model.SessionRecord
import com.vocare.translate.core.model.Turn
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/**
 * Room-backed [SessionHistoryStore] plus the read side History and Session
 * detail need. The list keeps a currently running server session visible
 * (a cloud session the device is in) but prefers the local record whenever
 * both stores hold the same id, exactly as the JSX did.
 */
class HistoryRepository(
    private val dao: SessionDao,
    private val api: VocaAccountApi,
    private val clientId: suspend () -> String,
    private val now: () -> Long = System::currentTimeMillis,
) : SessionHistoryStore {

    override suspend fun save(record: SessionRecord) {
        val at = now()
        val existing = dao.find(record.sessionId)
        dao.upsert(
            SessionEntity(
                sessionId = record.sessionId,
                callerName = record.callerName,
                topic = record.topic,
                languageA = record.languageA,
                languageB = record.languageB,
                participantA = record.participantA,
                participantB = record.participantB,
                status = record.status,
                durationSeconds = record.durationSeconds,
                transcriptJSON = json.encodeToString(ListSerializer(Turn.serializer()), record.transcript),
                startedAt = existing?.startedAt ?: at,
                updatedAt = at,
            ),
        )
    }

    suspend fun list(): List<SessionSummary> {
        val local = dao.list().map { it.toSummary() }
        val remoteLive = runCatching { api.sessions(clientId()) }.getOrDefault(emptyList())
            .filter { (it.status == "live" || it.status == "active") && local.none { l -> l.sessionId == it.sessionId } }
            .map {
                SessionSummary(
                    sessionId = it.sessionId,
                    callerName = it.callerName.orEmpty(),
                    topic = it.topic ?: "Translation Session",
                    lang = it.lang ?: "en",
                    status = it.status ?: "live",
                    durationLabel = it.duration,
                    participantCount = it.participantCount,
                )
            }
        return remoteLive + local
    }

    suspend fun detail(sessionId: String): SessionDetail? {
        dao.find(sessionId)?.let { row ->
            return SessionDetail(
                sessionId = row.sessionId,
                callerName = row.callerName,
                topic = row.topic,
                transcript = decodeTranscript(row.transcriptJSON),
            )
        }
        val remote = runCatching { api.sessionDetail(sessionId) }.getOrNull() ?: return null
        return SessionDetail(
            sessionId = remote.sessionId,
            callerName = remote.callerName.orEmpty(),
            topic = remote.topic ?: "Translation session",
            transcript = remote.transcript,
        )
    }

    private fun SessionEntity.toSummary() = SessionSummary(
        sessionId = sessionId,
        callerName = callerName,
        topic = topic,
        lang = languageA,
        status = status,
        durationLabel = formatDuration(durationSeconds),
        participantCount = 2,
    )

    companion object {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

        fun decodeTranscript(raw: String): List<Turn> =
            runCatching { json.decodeFromString(ListSerializer(Turn.serializer()), raw) }.getOrDefault(emptyList())

        fun formatDuration(secs: Int): String {
            val m = secs / 60
            val s = secs % 60
            return if (m > 0) "${m}m ${s}s" else "${s}s"
        }

        fun openDatabase(context: Context): HistoryDatabase =
            Room.databaseBuilder(context.applicationContext, HistoryDatabase::class.java, HistoryDatabase.NAME)
                .fallbackToDestructiveMigration(dropAllTables = true)
                .build()
    }
}
