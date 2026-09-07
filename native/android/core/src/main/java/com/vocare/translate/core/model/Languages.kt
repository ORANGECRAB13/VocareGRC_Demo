package com.vocare.translate.core.model

/**
 * The language table, as on VOCA `main`: Cantonese (`yue`) was withdrawn from
 * the picker and flags were added. `yue` is still resolvable through
 * [Language.of] so history recorded before the withdrawal renders correctly.
 */
data class Language(val code: String, val flag: String, val label: String) {
    companion object {
        val ALL: List<Language> = listOf(
            Language("en", "🇺🇸", "English"),
            Language("zh", "🇨🇳", "Mandarin"),
            Language("ja", "🇯🇵", "Japanese"),
            Language("ko", "🇰🇷", "Korean"),
            Language("es", "🇪🇸", "Spanish"),
            Language("fr", "🇫🇷", "French"),
            Language("de", "🇩🇪", "German"),
            Language("ar", "🇦🇪", "Arabic"),
            Language("hi", "🇮🇳", "Hindi"),
            Language("fil", "🇵🇭", "Filipino (Tagalog)"),
        )

        private val WITHDRAWN = Language("yue", "🇭🇰", "Cantonese")

        /** Never null: an unknown code renders as itself with a globe, like the JSX `getLang`. */
        fun of(code: String?): Language {
            val c = code.orEmpty()
            return ALL.firstOrNull { it.code == c }
                ?: WITHDRAWN.takeIf { it.code == c }
                ?: Language(c, "🌐", c)
        }
    }
}

/**
 * Locale strings for on-device speech, per CONTRACT §4. Recognition and
 * synthesis are separate because Android's downloadable Mandarin recognition
 * pack answers only to `cmn-Hans-CN`, while ML Kit / TTS want `zh-CN`.
 */
object SpeechLocales {
    private val SPEECH = mapOf(
        "en" to "en-US", "zh" to "zh-CN", "yue" to "zh-HK", "ja" to "ja-JP", "ko" to "ko-KR",
        "es" to "es-ES", "fr" to "fr-FR", "de" to "de-DE", "ar" to "ar-SA", "hi" to "hi-IN", "fil" to "fil-PH",
    )
    private val ANDROID_RECOGNITION = mapOf("zh" to "cmn-Hans-CN")

    fun speech(code: String): String = SPEECH[code] ?: code
    fun recognition(code: String): String = ANDROID_RECOGNITION[code] ?: speech(code)
}
