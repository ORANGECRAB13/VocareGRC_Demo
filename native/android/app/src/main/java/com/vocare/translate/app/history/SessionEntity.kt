package com.vocare.translate.app.history

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase

/**
 * Port of android/.../history/TranslationSessionEntity.java — same table, same
 * columns, same defaults, so a transcript written by the Capacitor app is
 * readable here should the two ever share a database file.
 */
@Entity(tableName = "translation_sessions")
data class SessionEntity(
    @PrimaryKey @ColumnInfo(name = "session_id") val sessionId: String,
    @ColumnInfo(name = "caller_name") val callerName: String = "Person A",
    @ColumnInfo(name = "topic") val topic: String = "Translation Session",
    @ColumnInfo(name = "language_a") val languageA: String = "en",
    @ColumnInfo(name = "language_b") val languageB: String = "zh",
    @ColumnInfo(name = "participant_a") val participantA: String = "Person A",
    @ColumnInfo(name = "participant_b") val participantB: String = "Person B",
    @ColumnInfo(name = "status") val status: String = "active",
    @ColumnInfo(name = "duration_seconds") val durationSeconds: Int = 0,
    @ColumnInfo(name = "transcript_json") val transcriptJSON: String = "[]",
    /** Epoch milliseconds. */
    @ColumnInfo(name = "started_at") val startedAt: Long = 0L,
    @ColumnInfo(name = "updated_at") val updatedAt: Long = 0L,
)

@Dao
interface SessionDao {
    @Query("SELECT * FROM translation_sessions WHERE session_id = :sessionId LIMIT 1")
    suspend fun find(sessionId: String): SessionEntity?

    /** Sorted by updatedAt descending, capped at 250 rows — mirrors the iOS FetchDescriptor. */
    @Query("SELECT * FROM translation_sessions ORDER BY updated_at DESC LIMIT 250")
    suspend fun list(): List<SessionEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: SessionEntity)
}

@Database(entities = [SessionEntity::class], version = 1, exportSchema = false)
abstract class HistoryDatabase : RoomDatabase() {
    abstract fun sessionDao(): SessionDao

    companion object {
        /** Also referenced by res/xml/backup_rules.xml and data_extraction_rules.xml. Keep in sync. */
        const val NAME = "vocare_translation_history.db"
    }
}
