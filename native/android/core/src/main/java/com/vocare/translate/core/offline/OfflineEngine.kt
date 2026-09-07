package com.vocare.translate.core.offline

/** Whether a language pair can run with no network (CONTRACT §4). */
enum class PairStatus {
    /** Both models are on the device: ready now. */
    INSTALLED,
    /** The pair has models but they are a download away. */
    SUPPORTED,
    /** No on-device model exists for this pair on this device. */
    UNSUPPORTED,
}

/** Languages with a first-party on-device model on Android (ML Kit; `fil` is `tl` there). */
object OfflineLanguages {
    val CODES: List<String> = listOf("en", "zh", "ja", "ko", "es", "fr", "de", "ar", "hi", "fil")
    private val MLKIT = mapOf("fil" to "tl")

    fun mlKitCode(code: String): String = MLKIT[code] ?: code

    /** Says nothing about downloads; only whether the pair could ever work offline. */
    fun canTranslate(a: String?, b: String?): Boolean =
        !a.isNullOrEmpty() && !b.isNullOrEmpty() && a != b && a in CODES && b in CODES
}

/** A recognition in progress: [stop] ends it and returns the final text; [cancel] discards it. */
interface Recognition {
    /** @throws Exception when nothing could be recognised (missing pack, timeout). */
    suspend fun stop(): String
    suspend fun cancel()
}

/**
 * On-device STT → MT → TTS, implemented in `:session` (ML Kit + SpeechRecognizer
 * + TextToSpeech), consumed by `:app` for Settings/Home pair status and installs.
 * Language arguments are Voca codes (`en`, `zh`, `fil`), not ML Kit codes.
 */
interface OfflineEngine {
    suspend fun pairStatus(a: String, b: String): PairStatus

    /**
     * Download whatever the pair is missing. `false` means the user declined —
     * not an error. Throws on a failed download.
     */
    suspend fun prepare(a: String, b: String): Boolean

    /**
     * Begin listening in [locale] (see `SpeechLocales.recognition`), on-device
     * only. [onPartial] is called on the main thread with the running transcript.
     */
    suspend fun startRecognition(locale: String, onPartial: (String) -> Unit): Recognition

    suspend fun translate(text: String, from: String, to: String): String

    /** Speak with a local voice only; returns when playback ends. Throws when no local voice exists. */
    suspend fun speak(text: String, locale: String)
}
