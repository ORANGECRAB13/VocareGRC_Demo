package com.vocare.translate.app.store

import android.app.Activity
import android.content.Context
import android.os.Handler
import android.os.Looper
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Play Billing 8.3.0 port of android/.../purchases/PurchasesPlugin.java — the
 * device-tested version. API usage, UI-thread handling, the queued connection,
 * duplicate-purchase rejection and "acknowledge only after the server verified
 * the token" are copied deliberately; only the callback plumbing became
 * coroutines.
 *
 * Play-specific rules this code depends on:
 *   - An unacknowledged purchase is auto-refunded after three days. We
 *     acknowledge only AFTER the server accepted the token, so a purchase we
 *     could not validate is refunded rather than silently kept.
 *   - queryPurchasesAsync is the restore path; Play has no separate restore API.
 *
 * Every BillingClient interaction happens on the main thread, as the plugin did.
 */
class PlayStoreClient(
    context: Context,
    /** Fires when Play reports a purchase outside of a flow we started (renewal, deferred approval). */
    private val onEntitlementChanged: () -> Unit = {},
) : StoreClient {

    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())

    private var billingClient: BillingClient? = null
    private var connecting = false
    private val readyCalls = mutableListOf<() -> Unit>()
    private val failCalls = mutableListOf<(String) -> Unit>()

    /** Set for the duration of a purchase flow; Play answers on a listener, not a callback. */
    private var pendingPurchase: CancellableContinuation<StorePurchaseResult>? = null

    private val purchasesUpdatedListener = PurchasesUpdatedListener { result, purchases ->
        val call = pendingPurchase
        pendingPurchase = null

        if (result.responseCode == BillingClient.BillingResponseCode.USER_CANCELED) {
            call?.resumeIfActive(StorePurchaseResult.Cancelled)
            return@PurchasesUpdatedListener
        }
        if (result.responseCode != BillingClient.BillingResponseCode.OK || purchases == null) {
            call?.resumeIfActive(StorePurchaseResult.Failed("purchase_failed: ${result.debugMessage}"))
            return@PurchasesUpdatedListener
        }
        for (purchase in purchases) {
            if (!purchase.products.contains(StoreClient.PRODUCT_ID)) continue
            if (purchase.purchaseState == Purchase.PurchaseState.PENDING) {
                // Slow payment methods (e.g. cash at a kiosk). Not an error: the
                // purchase lands on this listener again once it clears.
                call?.resumeIfActive(StorePurchaseResult.Pending)
                return@PurchasesUpdatedListener
            }
            if (purchase.purchaseState == Purchase.PurchaseState.PURCHASED) {
                val purchased = StorePurchaseResult.Purchased(purchase.purchaseToken, purchase.isAcknowledged)
                if (call != null) call.resumeIfActive(purchased) else onEntitlementChanged()
                return@PurchasesUpdatedListener
            }
        }
        call?.resumeIfActive(StorePurchaseResult.Failed("unknown"))
    }

    // ── Connection ────────────────────────────────────────────────

    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block() else mainHandler.post(block)
    }

    private fun withBilling(onFail: (String) -> Unit, ready: () -> Unit) = onMain { connectBilling(onFail, ready) }

    private fun connectBilling(onFail: (String) -> Unit, ready: () -> Unit) {
        val existing = billingClient
        if (existing != null && existing.isReady) {
            ready()
            return
        }
        readyCalls += ready
        failCalls += onFail
        if (connecting) return
        connecting = true
        billingClient?.endConnection()
        val client = BillingClient.newBuilder(appContext)
            .setListener(purchasesUpdatedListener)
            .enableAutoServiceReconnection()
            .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
            .build()
        billingClient = client

        client.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                connecting = false
                val callbacks = readyCalls.toList()
                val failures = failCalls.toList()
                readyCalls.clear()
                failCalls.clear()
                if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                    callbacks.forEach { it() }
                } else {
                    failures.forEach { it("billing_unavailable: ${result.debugMessage}") }
                }
            }

            override fun onBillingServiceDisconnected() {
                connecting = false
                val failures = failCalls.toList()
                readyCalls.clear()
                failCalls.clear()
                failures.forEach { it("billing_disconnected") }
            }
        })
    }

    private suspend fun <T> billing(block: (BillingClient, CancellableContinuation<T>) -> Unit): T =
        suspendCancellableCoroutine { cont ->
            withBilling(
                onFail = { cont.resumeIfActive(StoreUnavailableException(it)) },
                ready = { block(billingClient!!, cont) },
            )
        }

    // ── StoreClient ───────────────────────────────────────────────

    override suspend fun product(): StoreProduct = billing { client, cont ->
        queryProduct(client, cont::resumeIfActive) { details ->
            val offer = monthlyOffer(details)
            if (offer == null) {
                cont.resumeIfActive(StoreUnavailableException("monthly_subscription_unavailable"))
                return@queryProduct
            }
            val price = offer.pricingPhases.pricingPhaseList[0].formattedPrice
            cont.resumeIfActive(StoreProduct(details.productId, price, details.name, details.description))
        }
    }

    override suspend fun purchase(activity: Activity): StorePurchaseResult = suspendCancellableCoroutine { cont ->
        onMain {
            if (pendingPurchase != null) {
                cont.resumeIfActive(StorePurchaseResult.Failed("purchase_in_progress"))
                return@onMain
            }
            pendingPurchase = cont
            val fail: (Throwable) -> Unit = { error ->
                if (pendingPurchase === cont) pendingPurchase = null
                cont.resumeIfActive(error)
            }
            withBilling(onFail = { fail(StoreUnavailableException(it)) }) {
                val client = billingClient!!
                queryProduct(client, fail) { details ->
                    val offer = monthlyOffer(details)
                    if (offer == null) {
                        fail(StoreUnavailableException("monthly_subscription_unavailable"))
                        return@queryProduct
                    }
                    val params = BillingFlowParams.newBuilder()
                        .setProductDetailsParamsList(
                            listOf(
                                BillingFlowParams.ProductDetailsParams.newBuilder()
                                    .setProductDetails(details)
                                    .setOfferToken(offer.offerToken)
                                    .build(),
                            ),
                        )
                        .build()
                    val result = client.launchBillingFlow(activity, params)
                    if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                        pendingPurchase = null
                        cont.resumeIfActive(StorePurchaseResult.Failed("could_not_launch_billing: ${result.debugMessage}"))
                    }
                }
            }
        }
        cont.invokeOnCancellation { if (pendingPurchase === cont) pendingPurchase = null }
    }

    override suspend fun currentEntitlement(): HeldPurchase? = billing { client, cont ->
        client.queryPurchasesAsync(
            QueryPurchasesParams.newBuilder().setProductType(BillingClient.ProductType.SUBS).build(),
        ) { result, purchases ->
            if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                cont.resumeIfActive(StoreUnavailableException("query_purchases_failed: ${result.debugMessage}"))
                return@queryPurchasesAsync
            }
            val held = purchases.firstOrNull {
                it.products.contains(StoreClient.PRODUCT_ID) && it.purchaseState == Purchase.PurchaseState.PURCHASED
            }
            cont.resumeIfActive(held?.let { HeldPurchase(it.purchaseToken, it.isAcknowledged) })
        }
    }

    override suspend fun acknowledge(purchaseToken: String) {
        require(purchaseToken.isNotEmpty()) { "purchaseToken required" }
        billing<Unit> { client, cont ->
            client.acknowledgePurchase(
                AcknowledgePurchaseParams.newBuilder().setPurchaseToken(purchaseToken).build(),
            ) { result ->
                if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                    cont.resumeIfActive(Unit)
                } else {
                    cont.resumeIfActive(StoreUnavailableException("acknowledge_failed: ${result.debugMessage}"))
                }
            }
        }
    }

    fun destroy() = onMain {
        billingClient?.endConnection()
        billingClient = null
    }

    // ── Helpers ───────────────────────────────────────────────────

    private fun queryProduct(client: BillingClient, onFail: (Throwable) -> Unit, handler: (ProductDetails) -> Unit) {
        val params = QueryProductDetailsParams.newBuilder()
            .setProductList(
                listOf(
                    QueryProductDetailsParams.Product.newBuilder()
                        .setProductId(StoreClient.PRODUCT_ID)
                        .setProductType(BillingClient.ProductType.SUBS)
                        .build(),
                ),
            )
            .build()
        client.queryProductDetailsAsync(params) { result, queryResult ->
            val list = queryResult.productDetailsList
            if (result.responseCode != BillingClient.BillingResponseCode.OK || list.isEmpty()) {
                onFail(StoreUnavailableException("product_not_found: ${result.debugMessage}"))
                return@queryProductDetailsAsync
            }
            handler(list[0])
        }
    }

    /**
     * The paywall promises a monthly price, without a trial. Select the matching
     * base plan, not an arbitrary introductory offer or yearly plan.
     */
    private fun monthlyOffer(details: ProductDetails): ProductDetails.SubscriptionOfferDetails? =
        details.subscriptionOfferDetails?.firstOrNull { offer ->
            val phases = offer.pricingPhases.pricingPhaseList
            offer.offerId == null && phases.size == 1 &&
                phases[0].billingPeriod == "P1M" &&
                phases[0].recurrenceMode == ProductDetails.RecurrenceMode.INFINITE_RECURRING
        }
}

private fun <T> CancellableContinuation<T>.resumeIfActive(value: T) { if (isActive) resume(value) }
private fun <T> CancellableContinuation<T>.resumeIfActive(error: Throwable) { if (isActive) resumeWithException(error) }
