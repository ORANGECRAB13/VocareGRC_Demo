package com.vocare.translate.app.store

import com.android.billingclient.api.BillingClient

/**
 * Response-code handling for the two places Play can answer a purchase: the
 * `BillingResult` `launchBillingFlow` returns immediately, and the later
 * `PurchasesUpdatedListener` callback. Pulled out of [PlayStoreClient] so the
 * mapping is unit-testable without a device — the Play classes reached from
 * here are compile-time `int` constants only.
 *
 * `ITEM_ALREADY_OWNED` is not a failure. Play returns it when the account
 * already holds the subscription (reinstall before a restore ran, a second
 * device, a reviewer buying twice); the purchase exists, so the right move is
 * to go and fetch it and run it through verify-then-acknowledge.
 */
internal object BillingResponses {
    const val OK = BillingClient.BillingResponseCode.OK
    const val USER_CANCELED = BillingClient.BillingResponseCode.USER_CANCELED
    const val ITEM_ALREADY_OWNED = BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED

    /** The immediate result of `launchBillingFlow`. Null means the flow started; Play will answer on the listener. */
    fun launchOutcome(responseCode: Int, debugMessage: String?): StorePurchaseResult? = when (responseCode) {
        OK -> null
        ITEM_ALREADY_OWNED -> StorePurchaseResult.AlreadyOwned
        else -> StorePurchaseResult.Failed("could_not_launch_billing: ${debugMessage.orEmpty()}")
    }

    /** The `PurchasesUpdatedListener` result. Null means OK — inspect the purchase list instead. */
    fun updateOutcome(responseCode: Int, debugMessage: String?): StorePurchaseResult? = when (responseCode) {
        OK -> null
        USER_CANCELED -> StorePurchaseResult.Cancelled
        ITEM_ALREADY_OWNED -> StorePurchaseResult.AlreadyOwned
        else -> StorePurchaseResult.Failed("purchase_failed: ${debugMessage.orEmpty()}")
    }
}
