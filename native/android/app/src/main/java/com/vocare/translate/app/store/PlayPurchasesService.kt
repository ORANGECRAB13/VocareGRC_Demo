package com.vocare.translate.app.store

import android.app.Activity
import com.vocare.translate.app.api.VocaAccountApi
import com.vocare.translate.core.model.Balance

/** Outcome of a purchase or restore, after the server has had its say. */
sealed interface PurchaseOutcome {
    /** The server verified the receipt and granted pro. On Android the purchase is now acknowledged. */
    data class Purchased(val balance: Balance) : PurchaseOutcome
    data object Cancelled : PurchaseOutcome
    data object Pending : PurchaseOutcome
    /** Restore found nothing on this account. */
    data object NoSubscription : PurchaseOutcome
    /** The store handed over a receipt but the server could not verify it (yet). Nothing was acknowledged. */
    data class NotVerified(val balance: Balance) : PurchaseOutcome
    data class Failed(val reason: String) : PurchaseOutcome
}

/**
 * Purchase flow per CONTRACT §5: store purchase → `activate` with the receipt →
 * server says pro → **only now** acknowledge on Play. Restore is the same path
 * with `queryPurchasesAsync` as the source. [syncEntitlement] runs on launch
 * and resume: ask the store what is owned, tell the server, read the balance.
 */
class PlayPurchasesService(
    private val store: StoreClient?,
    private val api: VocaAccountApi,
    private val clientId: suspend () -> String,
) {
    val storeAvailable: Boolean get() = store != null

    suspend fun price(): String? = store?.let { runCatching { it.product().displayPrice }.getOrNull() }

    suspend fun purchasePro(activity: Activity): PurchaseOutcome {
        val client = store ?: return PurchaseOutcome.Failed("in_app_purchase_unavailable")
        val result = try {
            // The per-install client_id doubles as Play's obfuscated account id.
            client.purchase(activity, obfuscatedAccountId = clientId())
        } catch (e: StoreUnavailableException) {
            return PurchaseOutcome.Failed(e.message ?: "store_unavailable")
        }
        return when (result) {
            StorePurchaseResult.Cancelled -> PurchaseOutcome.Cancelled
            StorePurchaseResult.Pending -> PurchaseOutcome.Pending
            // Play refused to run a flow because this account already owns the
            // subscription (reinstall, second device, a reviewer buying twice).
            // The purchase exists, so this is a restore, not a failure.
            StorePurchaseResult.AlreadyOwned -> restore()
            is StorePurchaseResult.Failed -> PurchaseOutcome.Failed(result.reason)
            is StorePurchaseResult.Purchased -> {
                if (result.purchaseToken.isEmpty()) return PurchaseOutcome.Failed("purchase_receipt_missing")
                verifyAndAcknowledge(result.purchaseToken, result.acknowledged)
            }
        }
    }

    /**
     * Play's subscription-management page for this product, so cancellation is
     * never obstructed (Play policy) and the paywall's "cancel any time" copy is
     * true. Only meaningful in a build where the paid surface exists.
     */
    fun manageSubscriptionUrl(packageName: String): String =
        "https://play.google.com/store/account/subscriptions" +
            "?sku=${StoreClient.PRODUCT_ID}&package=$packageName"

    suspend fun restore(): PurchaseOutcome {
        val client = store ?: return PurchaseOutcome.Failed("in_app_purchase_unavailable")
        val held = try {
            client.currentEntitlement()
        } catch (e: StoreUnavailableException) {
            return PurchaseOutcome.Failed(e.message ?: "store_unavailable")
        } ?: return PurchaseOutcome.NoSubscription
        return verifyAndAcknowledge(held.purchaseToken, held.acknowledged)
    }

    /**
     * Launch/resume sync. `store_reachable:true` with an empty receipt is a
     * verified "no subscription" and may downgrade; a store we could not ask
     * must not, so that case only reads the balance back.
     */
    suspend fun syncEntitlement(): Balance {
        val subject = clientId()
        val client = store ?: return api.entitlement(subject)
        val held: HeldPurchase? = try {
            client.currentEntitlement()
        } catch (_: StoreUnavailableException) {
            return api.entitlement(subject)
        }
        val fresh = api.activate(subject, receipt = held?.purchaseToken.orEmpty(), storeReachable = true)
        if (held != null && fresh.verified == true && fresh.isPro && !held.acknowledged) {
            runCatching { client.acknowledge(held.purchaseToken) }
        }
        return fresh
    }

    private suspend fun verifyAndAcknowledge(token: String, alreadyAcknowledged: Boolean): PurchaseOutcome {
        val fresh = try {
            api.activate(clientId(), receipt = token, storeReachable = true)
        } catch (e: Exception) {
            return PurchaseOutcome.Failed("activate_failed: ${e.message}")
        }
        if (fresh.verified != true || !fresh.isPro) return PurchaseOutcome.NotVerified(fresh)
        // Only now is it safe to acknowledge; an unacknowledged purchase is
        // auto-refunded, which is the correct outcome if verification failed.
        if (!alreadyAcknowledged) {
            val client = store ?: return PurchaseOutcome.Failed("in_app_purchase_unavailable")
            try {
                client.acknowledge(token)
            } catch (e: Exception) {
                return PurchaseOutcome.Failed("acknowledge_failed: ${e.message}")
            }
        }
        return PurchaseOutcome.Purchased(fresh)
    }
}
