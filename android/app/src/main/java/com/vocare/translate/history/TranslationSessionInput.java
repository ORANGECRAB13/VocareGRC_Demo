package com.vocare.translate.history;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

/**
 * Mirror of the iOS {@code TranslationSessionInput} struct. Every optional field
 * is null when the caller omitted it, which the store reads as "leave unchanged".
 */
public class TranslationSessionInput {

    @NonNull
    public final String sessionId;
    @Nullable public final String callerName;
    @Nullable public final String topic;
    @Nullable public final String languageA;
    @Nullable public final String languageB;
    @Nullable public final String participantA;
    @Nullable public final String participantB;
    @Nullable public final String status;
    @Nullable public final Integer durationSeconds;
    @Nullable public final String transcriptJSON;
    /** Epoch milliseconds, or null to use "now". Only applied to newly created records. */
    @Nullable public final Long startedAt;

    public TranslationSessionInput(
        @NonNull String sessionId,
        @Nullable String callerName,
        @Nullable String topic,
        @Nullable String languageA,
        @Nullable String languageB,
        @Nullable String participantA,
        @Nullable String participantB,
        @Nullable String status,
        @Nullable Integer durationSeconds,
        @Nullable String transcriptJSON,
        @Nullable Long startedAt
    ) {
        this.sessionId = sessionId;
        this.callerName = callerName;
        this.topic = topic;
        this.languageA = languageA;
        this.languageB = languageB;
        this.participantA = participantA;
        this.participantB = participantB;
        this.status = status;
        this.durationSeconds = durationSeconds;
        this.transcriptJSON = transcriptJSON;
        this.startedAt = startedAt;
    }
}
