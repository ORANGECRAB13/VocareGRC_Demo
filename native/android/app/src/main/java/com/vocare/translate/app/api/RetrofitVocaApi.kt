package com.vocare.translate.app.api

import com.vocare.translate.core.api.ApiException
import com.vocare.translate.core.model.ActivateRequest
import com.vocare.translate.core.model.Balance
import com.vocare.translate.core.model.ConsumeRequest
import com.vocare.translate.core.model.CreateSessionRequest
import com.vocare.translate.core.model.CreateSessionResponse
import com.vocare.translate.core.model.ErrorBody
import com.vocare.translate.core.model.HangupRequest
import com.vocare.translate.core.model.HangupResponse
import com.vocare.translate.core.model.IceResponse
import com.vocare.translate.core.model.IceServer
import com.vocare.translate.core.model.OfferAnswer
import com.vocare.translate.core.model.OfferRequest
import com.vocare.translate.core.model.PaymentRequiredBody
import com.vocare.translate.core.model.PollResponse
import com.vocare.translate.core.model.PttAction
import com.vocare.translate.core.model.PttRequest
import com.vocare.translate.core.model.PttResult
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Retrofit declaration of CONTRACT §2. Every call returns [Response] so status codes are mapped in one place. */
interface VocaService {
    @GET("api/ice") suspend fun ice(): Response<IceResponse>
    @POST("api/translation/session") suspend fun createSession(@Body body: CreateSessionRequest): Response<CreateSessionResponse>
    @POST("api/translation/offer") suspend fun offer(@Body body: OfferRequest): Response<OfferAnswer>
    @POST("api/translation/ptt") suspend fun ptt(@Body body: PttRequest): Response<PttResult>
    @GET("api/translation/poll") suspend fun poll(@Query("session_id") sessionId: String): Response<PollResponse>
    @POST("api/hangup") suspend fun hangup(@Body body: HangupRequest): Response<HangupResponse>
    @GET("api/entitlement") suspend fun entitlement(@Query("subject") subject: String): Response<Balance>
    @POST("api/entitlement/activate") suspend fun activate(@Body body: ActivateRequest): Response<Balance>
    @POST("api/entitlement/consume") suspend fun consume(@Body body: ConsumeRequest): Response<Balance>
    @GET("api/translation/sessions") suspend fun sessions(@Query("client_id") clientId: String): Response<RemoteSessionList>
    @GET("api/translation/session/{id}") suspend fun sessionDetail(@Path("id") sessionId: String): Response<RemoteSessionDetail>
}

/**
 * [VocaAccountApi] over Retrofit + OkHttp + kotlinx-serialization. Maps HTTP
 * outcomes to [ApiException] subtypes; `hangup` and `consume` swallow failures
 * per the contract because they run during teardown.
 */
class RetrofitVocaApi internal constructor(private val service: VocaService) : VocaAccountApi {

    constructor(baseUrl: String, client: OkHttpClient = defaultClient()) : this(
        Retrofit.Builder()
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(VocaService::class.java),
    )

    override suspend fun iceServers(): List<IceServer> {
        val servers = call { service.ice() }.iceServers
        if (servers.isEmpty()) throw ApiException.NoIceServers()
        return servers
    }

    override suspend fun createSession(req: CreateSessionRequest): String = call { service.createSession(req) }.sessionId

    override suspend fun offer(req: OfferRequest): OfferAnswer = call { service.offer(req) }

    override suspend fun ptt(pcId: String, action: PttAction): PttResult = call { service.ptt(PttRequest(pcId, action)) }

    override suspend fun poll(sessionId: String): PollResponse = call { service.poll(sessionId) }

    override suspend fun hangup(pcId: String) {
        runCatching { call { service.hangup(HangupRequest(pcId)) } }
    }

    override suspend fun consume(subject: String, sessionId: String, seconds: Int): Balance? =
        runCatching { call { service.consume(ConsumeRequest(subject, sessionId, seconds)) } }.getOrNull()

    override suspend fun entitlement(subject: String): Balance = call { service.entitlement(subject) }

    override suspend fun activate(subject: String, receipt: String, storeReachable: Boolean): Balance =
        call { service.activate(ActivateRequest(subject = subject, receipt = receipt, storeReachable = storeReachable)) }

    override suspend fun sessions(clientId: String): List<RemoteSessionSummary> = call { service.sessions(clientId) }.sessions

    override suspend fun sessionDetail(sessionId: String): RemoteSessionDetail? =
        try {
            call { service.sessionDetail(sessionId) }
        } catch (e: ApiException.Http) {
            if (e.code == 404) null else throw e
        }

    private suspend fun <T : Any> call(block: suspend () -> Response<T>): T {
        val response = try {
            block()
        } catch (e: IOException) {
            throw ApiException.Network(e)
        } catch (e: SerializationException) {
            throw ApiException.Decode(e)
        } catch (e: IllegalArgumentException) {
            // Retrofit wraps some converter failures this way.
            throw ApiException.Decode(e)
        }
        if (response.isSuccessful) {
            return response.body() ?: throw ApiException.Decode(IllegalStateException("empty body"))
        }
        val raw = try { response.errorBody()?.string() } catch (_: IOException) { null }
        if (response.code() == 402) {
            val body = raw?.let { runCatching { json.decodeFromString<PaymentRequiredBody>(it) }.getOrNull() }
            throw ApiException.PaymentRequired(body?.balance)
        }
        val error = raw?.let { runCatching { json.decodeFromString<ErrorBody>(it).error }.getOrNull() }
            ?: raw?.takeIf { it.isNotBlank() && it.length < 200 }
        throw ApiException.Http(response.code(), error)
    }

    companion object {
        val json: Json = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
            coerceInputValues = true
        }

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}
