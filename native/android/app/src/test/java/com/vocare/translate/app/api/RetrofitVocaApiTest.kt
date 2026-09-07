package com.vocare.translate.app.api

import com.vocare.translate.app.Fixtures
import com.vocare.translate.core.api.ApiException
import com.vocare.translate.core.model.CreateSessionRequest
import com.vocare.translate.core.model.OfferRequest
import com.vocare.translate.core.model.PttAction
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Request and response encoding for every endpoint in CONTRACT §2, against a
 * real HTTP server serving the committed fixtures. Bodies are asserted on the
 * wire (snake_case, exact keys) because the backend does not change for us.
 */
class RetrofitVocaApiTest {

    private lateinit var server: MockWebServer
    private lateinit var api: RetrofitVocaApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = RetrofitVocaApi(server.url("/").toString())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun enqueue(body: String, code: Int = 200) {
        server.enqueue(MockResponse().setResponseCode(code).setBody(body).setHeader("Content-Type", "application/json"))
    }

    private fun jsonBody(raw: String) = Json.parseToJsonElement(raw).jsonObject

    @Test
    fun `ice servers decode both the string and array urls form`() = runBlocking {
        enqueue(Fixtures.read("ice.json"))
        val servers = api.iceServers()
        assertEquals(4, servers.size)
        assertEquals(listOf("stun:global.stun.twilio.com:3478"), servers[0].urls)
        assertEquals("FIXTURE_USERNAME", servers[1].username)
        assertEquals("FIXTURE_CREDENTIAL", servers[1].credential)
        assertEquals("GET", server.takeRequest().method)
    }

    @Test
    fun `an empty ice list fails the session rather than starting one`() = runBlocking {
        enqueue("""{"iceServers":[]}""")
        val thrown = runCatching { api.iceServers() }.exceptionOrNull()
        assertTrue("expected NoIceServers, got $thrown", thrown is ApiException.NoIceServers)
    }

    @Test
    fun `createSession posts snake_case fields and returns the session id`() = runBlocking {
        enqueue(Fixtures.read("session_create.json"))
        val id = api.createSession(
            CreateSessionRequest(
                callerName = "Alex",
                callerLanguage = "en",
                topic = "Translation Session",
                clientId = "client-123",
            ),
        )
        assertEquals("GRC-qa_Fixture_Session", id)

        val request = server.takeRequest()
        assertEquals("/api/translation/session", request.path)
        val body = jsonBody(request.body.readUtf8())
        assertEquals("Alex", body["caller_name"]?.jsonPrimitive?.content)
        assertEquals("en", body["caller_language"]?.jsonPrimitive?.content)
        assertEquals("client-123", body["client_id"]?.jsonPrimitive?.content)
        assertEquals("Translation Session", body["topic"]?.jsonPrimitive?.content)
    }

    @Test
    fun `offer posts the sdp and reads back the pc_id`() = runBlocking {
        enqueue(Fixtures.read("offer_answer.json"))
        val answer = api.offer(
            OfferRequest(sessionId = "s1", language = "en", name = "Alex", sdp = "v=0\r\n", type = "offer"),
        )
        assertEquals("pc-a-3f9c1d2e", answer.pcId)
        assertEquals("answer", answer.type)
        assertTrue(answer.sdp.startsWith("v=0"))

        val request = server.takeRequest()
        assertEquals("/api/translation/offer", request.path)
        val body = jsonBody(request.body.readUtf8())
        assertEquals("s1", body["session_id"]?.jsonPrimitive?.content)
        assertEquals("offer", body["type"]?.jsonPrimitive?.content)
        assertEquals("v=0\r\n", body["sdp"]?.jsonPrimitive?.content)
    }

    @Test
    fun `402 on offer becomes PaymentRequired carrying the balance`() = runBlocking {
        enqueue("""{"error":"no_translation_credit","balance":${Fixtures.read("balance_pro_exhausted.json")}}""", code = 402)
        val thrown = runCatching {
            api.offer(OfferRequest(sessionId = "s1", language = "en", name = "Alex", sdp = "v=0"))
        }.exceptionOrNull()

        assertTrue("expected PaymentRequired, got $thrown", thrown is ApiException.PaymentRequired)
        val balance = (thrown as ApiException.PaymentRequired).balance
        assertEquals("pro", balance?.tier)
        assertEquals(0, balance?.secondsRemaining)
    }

    @Test
    fun `a non-2xx that is not 402 becomes a typed Http error carrying the message`() = runBlocking {
        enqueue("""{"error":"session_not_found"}""", code = 404)
        val thrown = runCatching { api.poll("missing") }.exceptionOrNull()
        assertTrue("expected Http, got $thrown", thrown is ApiException.Http)
        assertEquals(404, (thrown as ApiException.Http).code)
        assertEquals("session_not_found", thrown.error)
    }

    @Test
    fun `ptt posts the action as the backend spells it`() = runBlocking {
        enqueue("""{"ok":true,"state":"holding"}""")
        val held = api.ptt("pc-a", PttAction.HOLD)
        assertTrue(held.ok)
        assertNull("hold has no flushed_bytes", held.flushedBytes)
        val request = server.takeRequest()
        assertEquals("/api/translation/ptt", request.path)
        val body = jsonBody(request.body.readUtf8())
        assertEquals("pc-a", body["pc_id"]?.jsonPrimitive?.content)
        assertEquals("hold", body["action"]?.jsonPrimitive?.content)

        enqueue("""{"ok":true,"state":"released","flushed_bytes":12800}""")
        val released = api.ptt("pc-a", PttAction.RELEASE)
        assertEquals(12800L, released.flushedBytes)
        assertEquals("release", jsonBody(server.takeRequest().body.readUtf8())["action"]?.jsonPrimitive?.content)
    }

    @Test
    fun `poll decodes turns, ignores unknown types and reports closed`() = runBlocking {
        enqueue(
            """{"events":[{"type":"turn","speaker":"pc-a-3f9c1d2e","original":"Hello","original_lang":"en",""" +
                """"translated":"你好"},{"type":"status","status":"live"}],"closed":false}""",
        )
        val response = api.poll("GRC-qa_Fixture_Session")
        assertEquals(2, response.events.size)
        assertTrue(response.events[0].isTurn)
        assertEquals("你好", response.events[0].translated)
        assertTrue("unknown types survive decoding", !response.events[1].isTurn)
        assertTrue(!response.closed)
        assertEquals("/api/translation/poll?session_id=GRC-qa_Fixture_Session", server.takeRequest().path)
    }

    @Test
    fun `hangup never throws and still posts the pc_id`() = runBlocking {
        enqueue("""{"ok":true,"found":true}""")
        api.hangup("pc-a")
        assertEquals("pc-a", jsonBody(server.takeRequest().body.readUtf8())["pc_id"]?.jsonPrimitive?.content)

        // A failing hangup during teardown must stay silent.
        enqueue("""{"error":"gone"}""", code = 500)
        api.hangup("pc-b")
    }

    @Test
    fun `consume posts total seconds and returns the new balance, null on failure`() = runBlocking {
        enqueue(Fixtures.read("balance_pro.json"))
        val balance = api.consume(subject = "client-123", sessionId = "s1", seconds = 45)
        assertEquals(2700, balance?.secondsRemaining)
        val body = jsonBody(server.takeRequest().body.readUtf8())
        assertEquals("client-123", body["subject"]?.jsonPrimitive?.content)
        assertEquals("s1", body["session_id"]?.jsonPrimitive?.content)
        assertEquals(45, body["session_seconds"]?.jsonPrimitive?.content?.toInt())

        enqueue("""{"error":"boom"}""", code = 500)
        assertNull(api.consume("client-123", "s1", 60))
    }

    @Test
    fun `entitlement decodes the balance payload`() = runBlocking {
        enqueue(Fixtures.read("balance_free_unenforced.json"))
        val balance = api.entitlement("client-123")
        assertEquals("free", balance.tier)
        assertEquals(false, balance.enforced)
        assertEquals("/api/entitlement?subject=client-123", server.takeRequest().path)
    }

    @Test
    fun `activate posts platform, receipt and store_reachable`() = runBlocking {
        enqueue(Fixtures.read("entitlement_activate_verified.json"))
        val balance = api.activate("client-123", receipt = "token-abc", storeReachable = true)
        assertEquals(true, balance.verified)
        assertTrue(balance.isPro)

        val body = jsonBody(server.takeRequest().body.readUtf8())
        assertEquals("android", body["platform"]?.jsonPrimitive?.content)
        assertEquals("token-abc", body["receipt"]?.jsonPrimitive?.content)
        assertEquals("true", body["store_reachable"]?.jsonPrimitive?.content)
    }

    @Test
    fun `an unverified activate is decoded as such rather than granting pro`() = runBlocking {
        enqueue(Fixtures.read("entitlement_activate_unverified.json"))
        val balance = api.activate("client-123", receipt = "bad", storeReachable = true)
        assertEquals(false, balance.verified)
        assertTrue(!balance.isPro)
    }

    @Test
    fun `session list and detail decode`() = runBlocking {
        enqueue("""{"sessions":[{"session_id":"s1","caller_name":"Alex","lang":"en","status":"live","duration":"01:20"}]}""")
        val rows = api.sessions("client-123")
        assertEquals(1, rows.size)
        assertEquals("s1", rows[0].sessionId)
        assertEquals("01:20", rows[0].duration)
        assertEquals("/api/translation/sessions?client_id=client-123", server.takeRequest().path)

        enqueue(Fixtures.read("session_detail.json"))
        val detail = api.sessionDetail("GRC-qa_Fixture_Session")
        assertEquals(6, detail?.transcript?.size)
        assertEquals("Alex", detail?.callerName)
        assertEquals("/api/translation/session/GRC-qa_Fixture_Session", server.takeRequest().path)
    }

    @Test
    fun `a missing session detail is null, not an exception`() = runBlocking {
        enqueue("""{"error":"not_found"}""", code = 404)
        assertNull(api.sessionDetail("gone"))
    }

    @Test
    fun `a transport failure becomes a typed Network error`() = runBlocking {
        server.shutdown()
        val thrown = runCatching { api.iceServers() }.exceptionOrNull()
        assertTrue("expected Network, got $thrown", thrown is ApiException.Network)
    }

    @Test
    fun `a 2xx body that cannot be parsed becomes a typed Decode error`() = runBlocking {
        enqueue("not json at all")
        val thrown = runCatching { api.createSession(CreateSessionRequest("Alex", "en", "T", "c")) }.exceptionOrNull()
        assertTrue("expected Decode, got $thrown", thrown is ApiException.Decode)
    }
}
