package com.vocare.translate.history;

import android.content.Context;

import androidx.room.Database;
import androidx.room.Room;
import androidx.room.RoomDatabase;

@Database(entities = { TranslationSessionEntity.class }, version = 1, exportSchema = false)
public abstract class TranslationHistoryDatabase extends RoomDatabase {

    /**
     * Also referenced by res/xml/backup_rules.xml and res/xml/data_extraction_rules.xml,
     * which exclude these files from cloud backup. Keep the three in sync.
     */
    public static final String DATABASE_NAME = "vocare_translation_history.db";

    private static volatile TranslationHistoryDatabase instance;

    public abstract TranslationSessionDao sessionDao();

    public static TranslationHistoryDatabase getInstance(Context context) {
        if (instance == null) {
            synchronized (TranslationHistoryDatabase.class) {
                if (instance == null) {
                    instance = Room
                        .databaseBuilder(
                            context.getApplicationContext(),
                            TranslationHistoryDatabase.class,
                            DATABASE_NAME
                        )
                        .fallbackToDestructiveMigration()
                        .build();
                }
            }
        }
        return instance;
    }
}
