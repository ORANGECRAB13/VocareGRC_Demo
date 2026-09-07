package com.vocare.translate.app.store

import android.app.Activity
import com.vocare.translate.app.Fixtures
import com.vocare.translate.app.api.RemoteSessionDetail
import com.vocare.translate.app.api.RemoteSessionSummary
import com.vocare.translate.app.api.VocaAccountApi
import com.vocare.translate.core.model.Balance
import com.vocare.translate.core.model.CreateSessionRequest
import com.vocare.translate.core.model.IceServer
import com.vocare.translate.core.model.OfferAnswer
import com.vocare.translate.core.model.OfferRequest
import com.vocare.translate.core.model.PollResponse
import com.vocare.translate.core.model.PttAction
import com.vocare.translate.core.model.PttResult
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private val json = Json { ignoreUnknownKeys = true }
private fun balance(name: String): Balance = json.decodeFromString(Fixtures.read(name))

/** Records what the store was asked to do, so "acknowledge only after verification" is observable. */
private class FakeStore(
    var purchaseResult: StorePurchaseResult = StorePurchaseResult.Purchased("token-abc", acknowledged = false),
    var held: HeldPurchase? = null,
    var throwOnQuery: Boolean = false,
) : StoreClient {
    val acknowledged = mutableListOf<String>()
    var purchaseCalls = 0

    /** Everything the store was told to do, in order, so ordering can be asserted. */
    val calls = mutableListOf<String>()

    override suspend fun product() = StoreProduct(StoreClient.PRODUCT_ID, "A$29.99", "Voca Pro", null)

    override suspend fun purchase(activity: Activity): StorePurchaseResult {
        purchaseCalls++
        calls += "purchase"
        return purchaseResult
    }

    override suspend fun currentEntitlement(): HeldPurchase? {
        calls += "query"
        if (throwOnQuery) throw StoreUnavailableException("billing_unavailable")
        return held
    }

    override suspend fun acknowledge(purchaseToken: String) {
        calls += "acknowledge"
        acknowledged += purchaseToken
    }
}

private class FakeAccountApi(
    var activateResult: Balance = Balance(tier = "pro", verified = true, secondsRemaining = 3600),
    var activateThrows: Boolean = false,
    var entitlementResult: Balance = Balance(tier = "free"),
) : VocaAccountApi {
    val activations = mutableListOf<Triple<String, String, Boolean>>()
    val calls = mutableListOf<String>()

    override suspend fun entitlement(subject: String): Balance {
        calls += "entitlement"
        return entitlementResult
    }

    override suspend fun activate(subject: String, receipt: String, storeReachable: Boolean): Balance {
        calls += "activate"
        activations += Triple(subject, receipt, storeReachable)
        if (activateThrows) throw IllegalStateException("server down")
        return activateResult
    }

    override suspend fun sessions(clientId: String): List<RemoteSessionSummary> = emptyList()
    override suspend fun sessionDetail(sessionId: String): RemoteSessionDetail? = null
    override suspend fun iceServers(): List<IceServer> = emptyList()
    override suspend fun createSession(req: CreateSessionRequest): String = "s1"
    override suspend fun offer(req: OfferRequest): OfferAnswer = OfferAnswer("pc", "sdp")
    override suspend fun ptt(pcId: String, action: PttAction): PttResult = PttResult()
    override suspend fun poll(sessionId: String): PollResponse = PollResponse()
    override suspend fun hangup(pcId: String) = Unit
    override suspend fun consume(subject: String, sessionId: String, seconds: Int): Balance? = null
}

/**
 * Purchase flow (CONTRACT §5). The load-bearing assertion is the ordering:
 * Play is only told to acknowledge *after* the server verified the receipt,
 * because an unacknowledged purchase is auto-refunded — which is the correct
 * outcome when we could not validate it.
 */
class PlayPurchasesServiceTest {

    private fun service(store: StoreClient?, api: VocaAccountApi) =
        PlayPurchasesService(store, api) { "client-123" }

    /**
     * The purchase flow only hands the Activity straight back to [StoreClient],
     * so a bare instance off the mockable android.jar is enough here.
     */
    private val activity: Activity = Activity()

    @Test
    fun `a verified purchase grants pro and acknowledges only afterwards`() = runBlocking {
        val store = FakeStore()
        val api = FakeAccountApi(activateResult = balance("entitlement_activate_verified.json"))
        val outcome = service(store, api).purchasePro(activity)

        assertTrue("expected Purchased, got $outcome", outcome is PurchaseOutcome.Purchased)
        assertTrue((outcome as PurchaseOutcome.Purchased).balance.isPro)
        assertEquals(listOf("token-abc"), store.acknowledged)
        assertEquals("acknowledge must be the last thing that happens", listOf("purchase", "acknowledge"), store.calls)
        assertEquals(listOf(Triple("client-123", "token-abc", true)), api.activations)
    }

    @Test
    fun `an unverified receipt is never acknowledged`() = runBlocking {
        val store = FakeStore()
        val api = FakeAccountApi(activateResult = balance("entitlement_activate_unverified.json"))
        val outcome = service(store, api).purchasePro(activity)

        assertTrue("expected NotVerified, got $outcome", outcome is PurchaseOutcome.NotVerified)
        assertTrue("an unacknowledged purchase is refunded — leave it that way", store.acknowledged.isEmpty())
    }

    @Test
    fun `a purchase the server could not be told about is never acknowledged`() = runBlocking {
        val store = FakeStore()
        val outcome = service(store, FakeAccountApi(activateThrows = true)).purchasePro(activity)
        assertTrue("expected Failed, got $outcome", outcome is PurchaseOutcome.Failed)
        assertTrue(store.acknowledged.isEmpty())
    }

    @Test
    fun `cancelling never reaches the server`() = runBlocking {
        val store = FakeStore(purchaseResult = StorePurchaseResult.Cancelled)
        val api = FakeAccountApi()
        assertEquals(PurchaseOutcome.Cancelled, service(store, api).purchasePro(activity))
        assertTrue(api.calls.isEmpty())
        assertTrue(store.acknowledged.isEmpty())
    }

    @Test
    fun `a pending purchase waits rather than granting pro`() = runBlocking {
        val store = FakeStore(purchaseResult = StorePurchaseResult.Pending)
        val api = FakeAccountApi()
        assertEquals(PurchaseOutcome.Pending, service(store, api).purchasePro(activity))
        assertTrue(api.calls.isEmpty())
        assertTrue(store.acknowledged.isEmpty())
    }

    @Test
    fun `a store failure is reported, not swallowed`() = runBlocking {
        val store = FakeStore(purchaseResult = StorePurchaseResult.Failed("developer_error"))
        val outcome = service(store, FakeAccountApi()).purchasePro(activity)
        assertEquals("developer_error", (outcome as PurchaseOutcome.Failed).reason)
    }

    @Test
    fun `restore verifies the held purchase and acknowledges it if Play had not`() = runBlocking {
        val store = FakeStore(held = HeldPurchase("token-restored", acknowledged = false))
        val api = FakeAccountApi(activateResult = balance("entitlement_activate_verified.json"))
        val outcome = service(store, api).restore()

        assertTrue("expected Purchased, got $outcome", outcome is PurchaseOutcome.Purchased)
        assertEquals(listOf("token-restored"), store.acknowledged)
        assertEquals(listOf("query", "acknowledge"), store.calls)
    }

    @Test
    fun `restore does not re-acknowledge a purchase Play already acknowledged`() = runBlocking {
        val store = FakeStore(held = HeldPurchase("token-restored", acknowledged = true))
        val api = FakeAccountApi(activateResult = balance("entitlement_activate_verified.json"))
        assertTrue(service(store, api).restore() is PurchaseOutcome.Purchased)
        assertTrue(store.acknowledged.isEmpty())
    }

    @Test
    fun `restore with nothing owned says so without calling the server`() = runBlocking {
        val store = FakeStore(held = null)
        val api = FakeAccountApi()
        assertEquals(PurchaseOutcome.NoSubscription, service(store, api).restore())
        assertTrue(api.calls.isEmpty())
    }

    @Test
    fun `with no store at all the flows fail cleanly`() = runBlocking {
        val service = service(null, FakeAccountApi())
        assertTrue(service.purchasePro(activity) is PurchaseOutcome.Failed)
        assertTrue(service.restore() is PurchaseOutcome.Failed)
        assertTrue(!service.storeAvailable)
        assertNull(service.price())
    }

    // ── syncEntitlement: store_reachable semantics (CONTRACT §2) ─────────────

    @Test
    fun `a reachable store with nothing owned activates with an empty receipt so the server may downgrade`() =
        runBlocking {
            val store = FakeStore(held = null)
            val api = FakeAccountApi(activateResult = balance("entitlement_activate_unverified.json"))
            val fresh = service(store, api).syncEntitlement()

            assertEquals(listOf(Triple("client-123", "", true)), api.activations)
            assertTrue(!fresh.isPro)
        }

    @Test
    fun `a store we could not ask must not downgrade — it only reads the balance back`() = runBlocking {
        val store = FakeStore(throwOnQuery = true)
        val api = FakeAccountApi(entitlementResult = balance("balance_pro.json"))
        val fresh = service(store, api).syncEntitlement()

        assertTrue("activate must not be called when the store is unreachable", api.activations.isEmpty())
        assertEquals(listOf("entitlement"), api.calls)
        assertTrue("the existing pro grant survives", fresh.isPro)
    }

    @Test
    fun `sync acknowledges a held purchase the server verified`() = runBlocking {
        val store = FakeStore(held = HeldPurchase("token-held", acknowledged = false))
        val api = FakeAccountApi(activateResult = balance("entitlement_activate_verified.json"))
        service(store, api).syncEntitlement()
        assertEquals(listOf("token-held"), store.acknowledged)
    }

    @Test
    fun `sync with no store client only reads the balance`() = runBlocking {
        val api = FakeAccountApi(entitlementResult = balance("balance_free_enforced.json"))
        val fresh = service(null, api).syncEntitlement()
        assertEquals(listOf("entitlement"), api.calls)
        assertEquals("free", fresh.tier)
    }

    @Test
    fun `the price comes from the store when it answers`() = runBlocking {
        assertEquals("A$29.99", service(FakeStore(), FakeAccountApi()).price())
    }
}
