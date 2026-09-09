package com.vocare.translate.app.store

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Play can answer a purchase in two places, and both must agree about
 * `ITEM_ALREADY_OWNED`: it is a purchase that already exists, not a failure.
 * Before this mapping existed the listener path turned it into
 * `purchase_failed: …`, which is what a reviewer testing twice would have seen.
 */
class BillingResponsesTest {

    @Test
    fun `ITEM_ALREADY_OWNED is a restore from the immediate launch result`() {
        assertEquals(
            StorePurchaseResult.AlreadyOwned,
            BillingResponses.launchOutcome(BillingResponses.ITEM_ALREADY_OWNED, "already owned"),
        )
    }

    @Test
    fun `ITEM_ALREADY_OWNED is a restore from the purchases listener too`() {
        assertEquals(
            StorePurchaseResult.AlreadyOwned,
            BillingResponses.updateOutcome(BillingResponses.ITEM_ALREADY_OWNED, "already owned"),
        )
    }

    @Test
    fun `OK is not an outcome — the flow or the purchase list decides`() {
        assertNull(BillingResponses.launchOutcome(BillingResponses.OK, ""))
        assertNull(BillingResponses.updateOutcome(BillingResponses.OK, ""))
    }

    @Test
    fun `cancellation and real failures keep their existing meaning`() {
        assertEquals(
            StorePurchaseResult.Cancelled,
            BillingResponses.updateOutcome(BillingResponses.USER_CANCELED, ""),
        )
        assertEquals(
            StorePurchaseResult.Failed("purchase_failed: boom"),
            BillingResponses.updateOutcome(6, "boom"),
        )
        assertEquals(
            StorePurchaseResult.Failed("could_not_launch_billing: boom"),
            BillingResponses.launchOutcome(5, "boom"),
        )
    }

    @Test
    fun `a cancelled flow is never mistaken for an owned subscription`() {
        assertEquals(
            StorePurchaseResult.Failed("could_not_launch_billing: cancelled"),
            BillingResponses.launchOutcome(BillingResponses.USER_CANCELED, "cancelled"),
        )
    }
}
