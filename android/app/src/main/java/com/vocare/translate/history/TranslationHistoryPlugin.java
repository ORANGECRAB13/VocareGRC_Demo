package com.vocare.translate.history;

import androidx.annotation.NonNull;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Android port of ios/App/App/TranslationHistoryPlugin.swift.
 *
 * The JS name, method names, payload keys and error strings are deliberately
 * identical to the iOS plugin so that static/vocare-app.jsx works unchanged on
 * both platforms.
 */
@CapacitorPlugin(name = "TranslationHistory")
public class TranslationHistoryPlugin extends Plugin {

    private static final String SESSION_ID_REQUIRED = "A sessionId is required.";
    private static final String SAVE_FAILED = "Could not save translation history.";
    private static final String LIST_FAILED = "Could not load translation history.";
    private static final String DETAIL_FAILED = "Could not load the translation session.";

    /** Room work never runs on the main thread. */
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @PluginMethod
    public void saveSession(final PluginCall call) {
        final String sessionId = call.getString("sessionId");
        if (sessionId == null || sessionId.isEmpty()) {
            call.reject(SESSION_ID_REQUIRED);
            return;
        }

        final TranslationSessionInput input = new TranslationSessionInput(
            sessionId,
            call.getString("callerName"),
            call.getString("topic"),
            call.getString("languageA"),
            call.getString("languageB"),
            call.getString("participantA"),
            call.getString("participantB"),
            call.getString("status"),
            call.getInt("durationSeconds"),
            call.getString("transcriptJSON"),
            null
        );

        executor.execute(() -> {
            try {
                TranslationSessionEntity record = store().upsert(input);
                JSObject result = new JSObject();
                result.put("session", summary(record));
                call.resolve(result);
            } catch (Exception error) {
                call.reject(SAVE_FAILED, error);
            }
        });
    }

    @PluginMethod
    public void listSessions(final PluginCall call) {
        executor.execute(() -> {
            try {
                List<TranslationSessionEntity> records = store().list();
                JSArray sessions = new JSArray();
                for (TranslationSessionEntity record : records) {
                    sessions.put(summary(record));
                }
                JSObject result = new JSObject();
                result.put("sessions", sessions);
                call.resolve(result);
            } catch (Exception error) {
                call.reject(LIST_FAILED, error);
            }
        });
    }

    @PluginMethod
    public void getSession(final PluginCall call) {
        final String sessionId = call.getString("sessionId");
        if (sessionId == null || sessionId.isEmpty()) {
            call.reject(SESSION_ID_REQUIRED);
            return;
        }

        executor.execute(() -> {
            try {
                TranslationSessionEntity record = store().get(sessionId);
                JSObject result = new JSObject();
                if (record == null) {
                    result.put("session", JSONObject.NULL);
                } else {
                    result.put("session", detail(record));
                }
                call.resolve(result);
            } catch (Exception error) {
                call.reject(DETAIL_FAILED, error);
            }
        });
    }

    private TranslationHistoryStore store() {
        return TranslationHistoryStore.getInstance(getContext());
    }

    /** Snake_case keys consumed verbatim by static/vocare-app.jsx. */
    private JSObject summary(@NonNull TranslationSessionEntity record) {
        JSObject value = new JSObject();
        value.put("session_id", record.sessionId);
        value.put("caller_name", record.callerName);
        value.put("topic", record.topic);
        value.put("lang", record.languageA);
        value.put("lang_a", record.languageA);
        value.put("lang_b", record.languageB);
        value.put("participant_count", 2);
        value.put("status", record.status);
        value.put("duration", record.durationSeconds);
        value.put("started_at", iso8601(record.startedAt));
        value.put("updated_at", iso8601(record.updatedAt));
        return value;
    }

    private JSObject detail(@NonNull TranslationSessionEntity record) {
        JSObject value = summary(record);
        JSArray transcript;
        try {
            transcript = new JSArray(record.transcriptJSON);
        } catch (Exception ignored) {
            transcript = new JSArray();
        }
        value.put("transcript", transcript);
        value.put("participant_a", record.participantA);
        value.put("participant_b", record.participantB);
        return value;
    }

    /** Matches Swift's ISO8601DateFormatter default output, e.g. 2026-08-29T04:12:33Z. */
    private static String iso8601(long epochMillis) {
        SimpleDateFormat formatter = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US);
        formatter.setTimeZone(TimeZone.getTimeZone("UTC"));
        return formatter.format(new Date(epochMillis));
    }
}
