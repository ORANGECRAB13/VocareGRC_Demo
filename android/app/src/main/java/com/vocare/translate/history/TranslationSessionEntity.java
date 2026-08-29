package com.vocare.translate.history;

import androidx.annotation.NonNull;
import androidx.room.ColumnInfo;
import androidx.room.Entity;
import androidx.room.PrimaryKey;

/**
 * Android mirror of the iOS {@code StoredTranslationSession} SwiftData model
 * (ios/App/App/TranslationHistoryStore.swift). Field names, defaults and
 * semantics are kept identical so both platforms expose the same JSON contract
 * to the shared web UI.
 */
@Entity(tableName = "translation_sessions")
public class TranslationSessionEntity {

    @PrimaryKey
    @NonNull
    @ColumnInfo(name = "session_id")
    public String sessionId = "";

    @ColumnInfo(name = "caller_name")
    public String callerName = "Person A";

    @ColumnInfo(name = "topic")
    public String topic = "Translation Session";

    @ColumnInfo(name = "language_a")
    public String languageA = "en";

    @ColumnInfo(name = "language_b")
    public String languageB = "zh";

    @ColumnInfo(name = "participant_a")
    public String participantA = "Person A";

    @ColumnInfo(name = "participant_b")
    public String participantB = "Person B";

    @ColumnInfo(name = "status")
    public String status = "active";

    @ColumnInfo(name = "duration_seconds")
    public int durationSeconds = 0;

    @ColumnInfo(name = "transcript_json")
    public String transcriptJSON = "[]";

    /** Epoch milliseconds; serialised as ISO 8601 at the plugin boundary. */
    @ColumnInfo(name = "started_at")
    public long startedAt = 0L;

    /** Epoch milliseconds; serialised as ISO 8601 at the plugin boundary. */
    @ColumnInfo(name = "updated_at")
    public long updatedAt = 0L;

    public TranslationSessionEntity() {
    }

    /** Matches the Swift {@code init(sessionId:startedAt:)} convenience initialiser. */
    public static TranslationSessionEntity newSession(@NonNull String sessionId, long startedAt) {
        TranslationSessionEntity entity = new TranslationSessionEntity();
        entity.sessionId = sessionId;
        entity.startedAt = startedAt;
        entity.updatedAt = startedAt;
        return entity;
    }
}
