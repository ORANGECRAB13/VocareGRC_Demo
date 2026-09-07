package com.vocare.translate.core.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive

// Wire models for CONTRACT §2. Field names follow the backend (snake_case) via
// @SerialName so the Kotlin side stays idiomatic. Decoding is lenient about
// unknown keys everywhere — the backend adds fields without notice.

/** `urls` arrives as a string from Twilio and as an array from the STUN fallback; accept both. */
object StringOrStringListSerializer : KSerializer<List<String>> {
    private val list = ListSerializer(String.serializer())
    override val descriptor: SerialDescriptor = list.descriptor

    override fun deserialize(decoder: Decoder): List<String> {
        val json = decoder as? JsonDecoder ?: return list.deserialize(decoder)
        return when (val element = json.decodeJsonElement()) {
            is JsonPrimitive -> listOf(element.content)
            is JsonArray -> element.map { it.jsonPrimitive.content }
            else -> emptyList()
        }
    }

    override fun serialize(encoder: Encoder, value: List<String>) = list.serialize(encoder, value)
}

@Serializable
data class IceServer(
    @Serializable(with = StringOrStringListSerializer::class)
    val urls: List<String>,
    val username: String? = null,
    val credential: String? = null,
)

@Serializable
data class IceResponse(@SerialName("iceServers") val iceServers: List<IceServer> = emptyList())

@Serializable
data class CreateSessionRequest(
    @SerialName("caller_name") val callerName: String,
    @SerialName("caller_language") val callerLanguage: String,
    val topic: String = "Translation Session",
    @SerialName("client_id") val clientId: String,
)

@Serializable
data class CreateSessionResponse(@SerialName("session_id") val sessionId: String, val status: String = "waiting")

@Serializable
data class OfferRequest(
    @SerialName("session_id") val sessionId: String,
    val language: String,
    val name: String,
    val sdp: String,
    val type: String = "offer",
)

@Serializable
data class OfferAnswer(@SerialName("pc_id") val pcId: String, val sdp: String, val type: String = "answer")

@Serializable
enum class PttAction {
    @SerialName("hold") HOLD,
    @SerialName("release") RELEASE;

    val wire: String get() = if (this == HOLD) "hold" else "release"
}

@Serializable
data class PttRequest(@SerialName("pc_id") val pcId: String, val action: PttAction)

/**
 * `/ptt` reply. On release, `flushed_bytes` is absent when nothing was
 * captured — the view model marks the side `ready` immediately in that case
 * instead of waiting for a `turn` event (CONTRACT §3.7).
 */
@Serializable
data class PttResult(
    val ok: Boolean = true,
    val state: String? = null,
    @SerialName("flushed_bytes") val flushedBytes: Long? = null,
    val error: String? = null,
)

/** One poll event. Unknown `type`s are carried through and ignored by consumers. */
@Serializable
data class PollEvent(
    val type: String,
    val speaker: String? = null,
    @SerialName("speaker_name") val speakerName: String? = null,
    @SerialName("original_lang") val originalLang: String? = null,
    @SerialName("translated_lang") val translatedLang: String? = null,
    val original: String? = null,
    val translated: String? = null,
    val status: String? = null,
    val text: String? = null,
    val reason: String? = null,
) {
    val isTurn: Boolean get() = type == "turn"
    val isTurnFailed: Boolean get() = type == "turn_failed"

    /** The transcript entry this event becomes when persisted. */
    fun toTurn(): Turn = Turn(
        speaker = speaker,
        speakerName = speakerName,
        originalLang = originalLang,
        translatedLang = translatedLang,
        original = original.orEmpty(),
        translated = translated,
    )
}

@Serializable
data class PollResponse(val events: List<PollEvent> = emptyList(), val closed: Boolean = false)

@Serializable
data class HangupRequest(@SerialName("pc_id") val pcId: String)

@Serializable
data class HangupResponse(val ok: Boolean = false, val found: Boolean = false)

@Serializable
data class ConsumeRequest(
    val subject: String,
    @SerialName("session_id") val sessionId: String,
    @SerialName("session_seconds") val sessionSeconds: Int,
)

@Serializable
data class ActivateRequest(
    val subject: String,
    val platform: String = "android",
    val receipt: String = "",
    @SerialName("store_reachable") val storeReachable: Boolean,
)

/**
 * Balance payload (CONTRACT §2). `enforced` is nullable because a *missing*
 * field means enforced; only an explicit `false` opens the cloud to everyone.
 */
@Serializable
data class Balance(
    val tier: String = "free",
    val period: String? = null,
    @SerialName("seconds_total") val secondsTotal: Int = 0,
    @SerialName("seconds_used") val secondsUsed: Int = 0,
    @SerialName("seconds_remaining") val secondsRemaining: Int = 0,
    val durable: Boolean = false,
    val enforced: Boolean? = null,
    @SerialName("sandbox_ok") val sandboxOk: Boolean = false,
    val verified: Boolean? = null,
) {
    val isPro: Boolean get() = tier == "pro"
    val metered: Boolean get() = enforced != false
    val cloudAllowed: Boolean get() = isPro || !metered

    /** Seconds a cloud session may run; infinite when the server refuses nobody. */
    val budgetSeconds: Double get() = if (metered) secondsRemaining.toDouble() else Double.POSITIVE_INFINITY
}

/** Body of a 402 from `/offer`. */
@Serializable
data class PaymentRequiredBody(val error: String? = null, val balance: Balance? = null)

/** Generic `{ "error": "..." }` body. */
@Serializable
data class ErrorBody(val error: String? = null)
