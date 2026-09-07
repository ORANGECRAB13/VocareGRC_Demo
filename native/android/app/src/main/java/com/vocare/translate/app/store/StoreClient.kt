package com.vocare.translate.app.store

import android.app.Activity

data class StoreProduct(val productId: String, val displayPrice: String, val displayName: String?, val description: String?)

/** A subscription this account already owns on this device. */
data class HeldPurchase(val purchaseToken: String, val acknowledged: Boolean)

sealed interface StorePurchaseResult {
    data class Purchased(val purchaseToken: String, val acknowledged: Boolean) : StorePurchaseResult
    data object Cancelled : StorePurchaseResult
    data object Pending : StorePurchaseResult
    data class Failed(val reason: String) : StorePurchaseResult
}

/** Thrown by [StoreClient] when Play cannot be reached at all (billing unavailable / disconnected). */
class StoreUnavailableException(message: String, cause: Throwable? = null) : Exception(message, cause)

/**
 * The store, abstracted so the purchase flow is testable. [PlayStoreClient] is
 * the Play Billing implementation; tests use a fake.
 */
interface StoreClient {
    suspend fun product(): StoreProduct
    suspend fun purchase(activity: Activity): StorePurchaseResult

    /** Play's restore path: `queryPurchasesAsync`. Null when nothing active is held. */
    suspend fun currentEntitlement(): HeldPurchase?
    suspend fun acknowledge(purchaseToken: String)

    companion object {
        /** Must match Play Console and PRO_PRODUCT_ID in vocare-app.jsx. */
        const val PRODUCT_ID = "vocare_pro_monthly"
        const val PRICE_FALLBACK = "A$29.99"
        const val PLAN_MINUTES = 60
    }
}
