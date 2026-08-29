package com.vocare.translate.history;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.room.Dao;
import androidx.room.Insert;
import androidx.room.OnConflictStrategy;
import androidx.room.Query;

import java.util.List;

@Dao
public interface TranslationSessionDao {

    @Query("SELECT * FROM translation_sessions WHERE session_id = :sessionId LIMIT 1")
    @Nullable
    TranslationSessionEntity find(@NonNull String sessionId);

    /** Sorted by updatedAt descending, capped at 250 rows — mirrors the iOS FetchDescriptor. */
    @Query("SELECT * FROM translation_sessions ORDER BY updated_at DESC LIMIT 250")
    @NonNull
    List<TranslationSessionEntity> list();

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsert(@NonNull TranslationSessionEntity entity);
}
