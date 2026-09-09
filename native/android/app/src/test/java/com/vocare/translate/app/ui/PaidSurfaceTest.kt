package com.vocare.translate.app.ui

import com.vocare.translate.app.Fixtures
import com.vocare.translate.app.store.EntitlementStore
import com.vocare.translate.app.store.PaywallReason
import com.vocare.translate.app.store.StartRoute
import com.vocare.translate.core.model.Balance
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The build-time kill switch. With it off the app must have no paywall
 * destination at all — [VocaNavHost] registers `composable(Routes.PAYWALL)`
 * inside `if (PaidSurface.ENABLED)`, and every navigation into it goes through
 * [PaidSurface.paywallRoute], so a null here is a route that does not exist.
 */
class PaidSurfaceTest {

    private val json = Json { ignoreUnknownKeys = true }
    private fun balance(name: String): Balance = json.decodeFromString(Fixtures.read(name))

    @Test
    fun `the shipped build has the paid surface switched off`() {
        assertFalse("the free build must not be able to reach a paywall", PaidSurface.ENABLED)
        assertNull(PaidSurface.paywallRoute(PaywallReason.UPSELL))
        assertNull(PaidSurface.paywallRoute(PaywallReason.EXHAUSTED))
        assertNull(PaidSurface.paywallRoute(PaywallReason.UNSUPPORTED))
    }

    @Test
    fun `a paid build keeps every paywall variant addressable`() {
        assertEquals("paywall/UPSELL", PaidSurface.paywallRoute(PaywallReason.UPSELL, enabled = true))
        assertEquals("paywall/EXHAUSTED", PaidSurface.paywallRoute(PaywallReason.EXHAUSTED, enabled = true))
        assertNotNull(PaidSurface.paywallRoute(PaywallReason.UNSUPPORTED, enabled = true))
    }

    @Test
    fun `Start never lands on a paywall in a build that has none`() {
        val exhausted = StartRoute.Paywall(PaywallReason.EXHAUSTED)
        assertEquals(StartRoute.Cloud, PaidSurface.resolve(exhausted, enabled = false))
        assertEquals(
            StartRoute.Cloud,
            PaidSurface.resolve(StartRoute.Paywall(PaywallReason.UNSUPPORTED), enabled = false),
        )
        assertEquals("the paywall stays intact once the paid tier is on", exhausted, PaidSurface.resolve(exhausted, enabled = true))
    }

    @Test
    fun `free mode still starts a cloud session and shows no upsell`() {
        val free = EntitlementStore.Snapshot(balance("balance_free_unenforced.json"), offlinePref = false)
        val route = free.route(pairOfflineCapable = true)
        assertEquals(StartRoute.Cloud, route)
        assertEquals(StartRoute.Cloud, PaidSurface.resolve(route))
        assertFalse("no upgrade affordance in free mode", free.paidSurfaceVisible)
        assertTrue("free is ad-supported even with metering off", free.showAds)
    }

    @Test
    fun `offline and cloud verdicts pass through untouched`() {
        assertEquals(StartRoute.Offline, PaidSurface.resolve(StartRoute.Offline, enabled = false))
        assertEquals(StartRoute.Cloud, PaidSurface.resolve(StartRoute.Cloud, enabled = false))
    }
}
