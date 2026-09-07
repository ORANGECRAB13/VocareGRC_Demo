package com.vocare.translate.core.api

import com.vocare.translate.core.model.Balance
import com.vocare.translate.core.model.CreateSessionRequest
import com.vocare.translate.core.model.IceServer
import com.vocare.translate.core.model.OfferAnswer
import com.vocare.translate.core.model.OfferRequest
import com.vocare.translate.core.model.PollResponse
import com.vocare.translate.core.model.PttAction
import com.vocare.translate.core.model.PttResult

/**
 * Typed failures from the backend. Every [VocaApi] method that can throw throws
 * one of these (never a raw IOException), so callers route on type.
 */
sealed class ApiException(message: String, cause: Throwable? = null) : Exception(message, cause) {
    /** 402 from `/offer`: metering is enforced and the balance is 0. */
    class PaymentRequired(val balance: Balance?) : ApiException("no_translation_credit")

    /** Any other non-2xx. [error] is the backend's `{ "error": ... }` when it sent one. */
    class Http(val code: Int, val error: String?) : ApiException("http_$code" + (error?.let { ": $it" } ?: ""))

    /** Transport failure: DNS, timeout, connection reset, TLS. */
    class Network(cause: Throwable) : ApiException("network_error", cause)

    /** 2xx with a body we could not parse. */
    class Decode(cause: Throwable) : ApiException("decode_error", cause)

    /** `/api/ice` returned an empty list — the session must fail before any offer (CONTRACT §2). */
    class NoIceServers : ApiException("no_ice_servers")
}

/**
 * The backend, as `:session` sees it (CONTRACT §6). Implemented in `:app`
 * (Retrofit) and injected. Entitlement/history endpoints the session does not
 * need live on `:app`'s own extension of this interface.
 */
interface VocaApi {
    /** @throws ApiException.NoIceServers when the list is empty. */
    suspend fun iceServers(): List<IceServer>

    /** @return the new `session_id`. */
    suspend fun createSession(req: CreateSessionRequest): String

    /** @throws ApiException.PaymentRequired on 402. */
    suspend fun offer(req: OfferRequest): OfferAnswer

    suspend fun ptt(pcId: String, action: PttAction): PttResult

    suspend fun poll(sessionId: String): PollResponse

    /** Fire-and-forget; never throws. */
    suspend fun hangup(pcId: String)

    /** Reports the session's *total* elapsed seconds. Never throws; null when the report failed. */
    suspend fun consume(subject: String, sessionId: String, seconds: Int): Balance?
}
