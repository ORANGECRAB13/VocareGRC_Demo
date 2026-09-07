package com.vocare.translate.session.offline

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import com.google.mlkit.common.model.DownloadConditions
import com.google.mlkit.common.model.RemoteModelManager
import com.google.mlkit.nl.translate.TranslateLanguage
import com.google.mlkit.nl.translate.TranslateRemoteModel
import com.google.mlkit.nl.translate.Translation
import com.google.mlkit.nl.translate.Translator
import com.google.mlkit.nl.translate.TranslatorOptions
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.offline.OfflineEngine
import com.vocare.translate.core.offline.OfflineLanguages
import com.vocare.translate.core.offline.PairStatus
import com.vocare.translate.core.offline.Recognition
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class OfflineSpeechException(message: String) : Exception(message)

/**
 * On-device pipeline for Android (CONTRACT §4): `SpeechRecognizer` preferring
 * offline, ML Kit translation with self-service model download, and
 * `TextToSpeech` restricted to voices that do not need the network.
 *
 * Nothing in here touches `VocaApi`. All Android speech objects are created and
 * driven on [mainDispatcher] — `SpeechRecognizer` must be constructed on the main
 * thread (Android 16 terminates the process otherwise; the Capacitor plugin's
 * worker-thread probe was the crash recorded in
 * docs/DEPLOYMENT-CANDIDATE-2026-09-05.md).
 */
class AndroidOfflineEngine(
    context: Context,
    private val mainDispatcher: CoroutineDispatcher = Dispatchers.Main,
) : OfflineEngine {
    private val appContext = context.applicationContext
    private val modelManager = RemoteModelManager.getInstance()
    private val translators = mutableMapOf<String, Translator>()

    private var activeRecognition: SpeechCapture? = null
    private var tts: TextToSpeech? = null
    private var ttsReady: CompletableDeferred<Boolean>? = null

    // ── Language packs ───────────────────────────────────────────────────────

    override suspend fun pairStatus(a: String, b: String): PairStatus {
        if (!OfflineLanguages.canTranslate(a, b)) return PairStatus.UNSUPPORTED
        val installed = modelManager.getDownloadedModels(TranslateRemoteModel::class.java).await()
            .map { it.language }
            .toSet()
        val have = OfflineLanguages.mlKitCode(a) in installed && OfflineLanguages.mlKitCode(b) in installed
        return if (have) PairStatus.INSTALLED else PairStatus.SUPPORTED
    }

    /**
     * ML Kit installs one model per language; `downloadModelIfNeeded` on the a→b
     * translator fetches both. Models are ~30MB and download over any connection,
     * so the caller confirms with the user first — ML Kit itself never prompts,
     * which is why this returns `true` on completion and only throws on failure.
     */
    override suspend fun prepare(a: String, b: String): Boolean {
        if (!OfflineLanguages.canTranslate(a, b)) return false
        translator(a, b).downloadModelIfNeeded(DownloadConditions.Builder().build()).await()
        return true
    }

    override suspend fun translate(text: String, from: String, to: String): String {
        if (!OfflineLanguages.canTranslate(from, to)) {
            throw OfflineSpeechException("${Language.of(from).label} to ${Language.of(to).label} is not available on this device.")
        }
        return translator(from, to).translate(text).await().trim()
    }

    private fun translator(from: String, to: String): Translator {
        val key = "$from>$to"
        return translators.getOrPut(key) {
            val source = TranslateLanguage.fromLanguageTag(OfflineLanguages.mlKitCode(from))
                ?: throw OfflineSpeechException("ML Kit does not know $from")
            val target = TranslateLanguage.fromLanguageTag(OfflineLanguages.mlKitCode(to))
                ?: throw OfflineSpeechException("ML Kit does not know $to")
            Translation.getClient(
                TranslatorOptions.Builder().setSourceLanguage(source).setTargetLanguage(target).build(),
            )
        }
    }

    // ── Recognition ──────────────────────────────────────────────────────────

    /**
     * Starts listening in [locale] and returns once the recognizer has accepted
     * the request. Partials go to [onPartial] on the main thread. `stop()` ends
     * capture and returns the final transcript (empty for `NO_MATCH` /
     * `SPEECH_TIMEOUT`); an unsupported locale or missing pack surfaces as
     * [OfflineSpeechException] so the screen can explain instead of silently
     * doing nothing.
     */
    override suspend fun startRecognition(locale: String, onPartial: (String) -> Unit): Recognition =
        withContext(mainDispatcher) {
            // isRecognitionAvailable is the thread-safe probe; the on-device-specific probe
            // is what crashed the Capacitor build, so we let startListening report unsupported.
            if (!SpeechRecognizer.isRecognitionAvailable(appContext)) {
                throw OfflineSpeechException("Speech recognition is not available on this device.")
            }
            activeRecognition?.cancel()
            val capture = SpeechCapture(SpeechRecognizer.createSpeechRecognizer(appContext), onPartial)
            activeRecognition = capture
            capture.start(locale)
            capture
        }

    private inner class SpeechCapture(
        private val recognizer: SpeechRecognizer,
        private val onPartial: (String) -> Unit,
    ) : Recognition {
        private val result = CompletableDeferred<String>()
        private var lastPartial = ""
        private var destroyed = false

        fun start(locale: String) {
            recognizer.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {}
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {}
                override fun onEvent(eventType: Int, params: Bundle?) {}

                override fun onPartialResults(partialResults: Bundle?) {
                    val text = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull().orEmpty()
                    if (text.isNotEmpty()) {
                        lastPartial = text
                        onPartial(text)
                    }
                }

                override fun onResults(results: Bundle?) {
                    val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull().orEmpty()
                    result.complete(text.ifEmpty { lastPartial })
                }

                override fun onError(error: Int) {
                    when (error) {
                        SpeechRecognizer.ERROR_NO_MATCH,
                        SpeechRecognizer.ERROR_SPEECH_TIMEOUT,
                        -> result.complete(lastPartial)
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> result.completeExceptionally(
                            OfflineSpeechException("Speech recognition permission was declined. Enable microphone access for Voca in your device settings."),
                        )
                        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED,
                        SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE,
                        -> result.completeExceptionally(
                            OfflineSpeechException("On-device recognition for this language is not installed — download the recognition language in your device speech settings."),
                        )
                        else -> result.completeExceptionally(OfflineSpeechException("On-device speech recognition failed (error $error)."))
                    }
                }
            })
            recognizer.startListening(
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, locale)
                    putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                    putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                    putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, appContext.packageName)
                },
            )
        }

        /** Release: stop capturing, wait (bounded) for the final result. */
        override suspend fun stop(): String = withContext(mainDispatcher) {
            try {
                if (!result.isCompleted) runCatching { recognizer.stopListening() }
                // The recognizer normally answers within a second or two; never hang the screen.
                val text = withTimeoutOrNull(STOP_TIMEOUT_MS) { result.await() }
                    ?: throw OfflineSpeechException("Speech recognition timed out. Please try again.")
                text.trim()
            } finally {
                destroy()
            }
        }

        override suspend fun cancel() = withContext(mainDispatcher) {
            runCatching { recognizer.cancel() }
            if (!result.isCompleted) result.cancel()
            destroy()
        }

        /** Synchronous main-thread teardown used by [dispose]. */
        fun abandon() {
            runCatching { recognizer.cancel() }
            if (!result.isCompleted) result.cancel()
            destroy()
        }

        private fun destroy() {
            if (destroyed) return
            destroyed = true
            runCatching { recognizer.destroy() }
            if (activeRecognition === this) activeRecognition = null
        }
    }

    // ── Synthesis ────────────────────────────────────────────────────────────

    override suspend fun speak(text: String, locale: String) = withContext(mainDispatcher) {
        val engine = engine()
        val voice = localVoice(engine, locale)
            ?: throw OfflineSpeechException("No offline voice is installed for this language. Download a voice in your device text-to-speech settings.")
        engine.voice = voice
        engine.setSpeechRate(1.0f)
        val utteranceId = UUID.randomUUID().toString()
        suspendCancellableCoroutine { cont ->
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(id: String?) {}
                override fun onDone(id: String?) {
                    if (id == utteranceId && cont.isActive) cont.resume(Unit)
                }

                @Deprecated("Deprecated in Java")
                override fun onError(id: String?) {
                    if (id == utteranceId && cont.isActive) cont.resumeWithException(OfflineSpeechException("Could not speak the translation offline."))
                }

                override fun onError(id: String?, errorCode: Int) {
                    if (id == utteranceId && cont.isActive) cont.resumeWithException(OfflineSpeechException("Could not speak the translation offline (error $errorCode)."))
                }

                override fun onStop(id: String?, interrupted: Boolean) {
                    if (id == utteranceId && cont.isActive) cont.resume(Unit)
                }
            })
            cont.invokeOnCancellation { runCatching { engine.stop() } }
            val queued = engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
            if (queued != TextToSpeech.SUCCESS && cont.isActive) {
                cont.resumeWithException(OfflineSpeechException("Could not speak the translation offline."))
            }
        }
    }

    private suspend fun engine(): TextToSpeech {
        val existing = tts
        val existingReady = ttsReady
        if (existing != null && existingReady != null && existingReady.await()) return existing
        val ready = CompletableDeferred<Boolean>()
        ttsReady = ready
        val created = TextToSpeech(appContext) { status -> ready.complete(status == TextToSpeech.SUCCESS) }
        tts = created
        if (!ready.await()) throw OfflineSpeechException("On-device speech synthesis is unavailable.")
        return created
    }

    /** JSX `localTtsVoice`: exact locale first, then language, local voices only. */
    private fun localVoice(engine: TextToSpeech, locale: String): Voice? {
        val voices = runCatching { engine.voices }.getOrNull().orEmpty()
            .filter { !it.isNetworkConnectionRequired && TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features }
        val target = normalize(locale)
        return voices.firstOrNull { normalize(it.locale.toLanguageTag()) == target }
            ?: voices.firstOrNull { normalize(it.locale.toLanguageTag()).substringBefore('-') == target.substringBefore('-') }
    }

    private fun normalize(tag: String) = tag.lowercase().replace('_', '-')

    /** Stop everything in flight and free the engines. Call from the main thread. */
    fun dispose() {
        activeRecognition?.abandon()
        activeRecognition = null
        runCatching { tts?.stop(); tts?.shutdown() }
        tts = null
        ttsReady = null
        translators.values.forEach { runCatching { it.close() } }
        translators.clear()
    }

    private companion object {
        const val STOP_TIMEOUT_MS = 4_000L
    }
}
