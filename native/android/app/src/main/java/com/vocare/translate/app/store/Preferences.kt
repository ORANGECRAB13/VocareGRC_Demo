package com.vocare.translate.app.store

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.util.UUID

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "voca_prefs")

/**
 * Per-install preferences (CONTRACT §5 Persistence). The offline switch is
 * tri-state: absent until the user touches it, then sticks (§5 Tiers).
 */
class VocaPreferences(context: Context) {
    private val store = context.applicationContext.dataStore

    val offlinePref: Flow<Boolean?> = store.data.map { it[OFFLINE] }
    val micConsent: Flow<Boolean> = store.data.map { it[MIC_CONSENT] == true }
    val settings: Flow<SessionSettings> = store.data.map { prefs ->
        SessionSettings(
            notes = prefs[NOTES] ?: true,
            saveTranscripts = prefs[SAVE] ?: true,
            speakAloud = prefs[AUTOPLAY] ?: true,
            haptics = prefs[HAPTICS] ?: true,
            largeText = prefs[LARGE] ?: false,
        )
    }

    /**
     * Opaque per-install UUID. Not a credential: it only scopes history and the
     * credit balance to this install. Generated once and persisted.
     */
    suspend fun clientId(): String {
        store.data.first()[CLIENT_ID]?.let { return it }
        val fresh = UUID.randomUUID().toString()
        store.edit { prefs -> if (prefs[CLIENT_ID] == null) prefs[CLIENT_ID] = fresh }
        return store.data.first()[CLIENT_ID] ?: fresh
    }

    suspend fun setOfflinePref(value: Boolean) = store.edit { it[OFFLINE] = value }
    suspend fun recordMicConsent() = store.edit { it[MIC_CONSENT] = true }

    suspend fun setSetting(key: SettingKey, value: Boolean) = store.edit {
        it[
            when (key) {
                SettingKey.NOTES -> NOTES
                SettingKey.SAVE -> SAVE
                SettingKey.AUTOPLAY -> AUTOPLAY
                SettingKey.HAPTICS -> HAPTICS
                SettingKey.LARGE -> LARGE
            },
        ] = value
    }

    private companion object {
        val CLIENT_ID = stringPreferencesKey("vocare.clientId")
        val OFFLINE = booleanPreferencesKey("vocare.offlineMode")
        val MIC_CONSENT = booleanPreferencesKey("vocare.micConsent.v1")
        val NOTES = booleanPreferencesKey("settings.notes")
        val SAVE = booleanPreferencesKey("settings.save")
        val AUTOPLAY = booleanPreferencesKey("settings.autoplay")
        val HAPTICS = booleanPreferencesKey("settings.haptics")
        val LARGE = booleanPreferencesKey("settings.large")
    }
}

enum class SettingKey { NOTES, SAVE, AUTOPLAY, HAPTICS, LARGE }

data class SessionSettings(
    val notes: Boolean = true,
    val saveTranscripts: Boolean = true,
    val speakAloud: Boolean = true,
    val haptics: Boolean = true,
    val largeText: Boolean = false,
) {
    operator fun get(key: SettingKey): Boolean = when (key) {
        SettingKey.NOTES -> notes
        SettingKey.SAVE -> saveTranscripts
        SettingKey.AUTOPLAY -> speakAloud
        SettingKey.HAPTICS -> haptics
        SettingKey.LARGE -> largeText
    }
}
