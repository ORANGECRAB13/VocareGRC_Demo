package com.vocare.translate.history;

import android.content.Context;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.util.List;

/**
 * Android mirror of the iOS {@code TranslationHistoryStore}. Every method here
 * touches Room and therefore must be called off the main thread; the plugin
 * dispatches onto a dedicated single-thread executor.
 */
public class TranslationHistoryStore {

    private static volatile TranslationHistoryStore instance;

    private final TranslationSessionDao dao;

    private TranslationHistoryStore(Context context) {
        this.dao = TranslationHistoryDatabase.getInstance(context).sessionDao();
    }

    public static TranslationHistoryStore getInstance(Context context) {
        if (instance == null) {
            synchronized (TranslationHistoryStore.class) {
                if (instance == null) {
                    instance = new TranslationHistoryStore(context);
                }
            }
        }
        return instance;
    }

    /**
     * Insert-or-merge by sessionId. A non-null incoming value overwrites the stored
     * one; a null value leaves the stored value alone. {@code updatedAt} always moves.
     */
    @NonNull
    public TranslationSessionEntity upsert(@NonNull TranslationSessionInput input) {
        long now = System.currentTimeMillis();
        TranslationSessionEntity record = dao.find(input.sessionId);
        if (record == null) {
            record = TranslationSessionEntity.newSession(
                input.sessionId,
                input.startedAt != null ? input.startedAt : now
            );
        }

        if (input.callerName != null) record.callerName = input.callerName;
        if (input.topic != null) record.topic = input.topic;
        if (input.languageA != null) record.languageA = input.languageA;
        if (input.languageB != null) record.languageB = input.languageB;
        if (input.participantA != null) record.participantA = input.participantA;
        if (input.participantB != null) record.participantB = input.participantB;
        if (input.status != null) record.status = input.status;
        if (input.durationSeconds != null) record.durationSeconds = input.durationSeconds;
        if (input.transcriptJSON != null) record.transcriptJSON = input.transcriptJSON;
        record.updatedAt = now;

        dao.upsert(record);
        return record;
    }

    @NonNull
    public List<TranslationSessionEntity> list() {
        return dao.list();
    }

    @Nullable
    public TranslationSessionEntity get(@NonNull String sessionId) {
        return dao.find(sessionId);
    }
}
