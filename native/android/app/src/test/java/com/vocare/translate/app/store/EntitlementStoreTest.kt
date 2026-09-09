package com.vocare.translate.app.store

import com.vocare.translate.app.Fixtures
import com.vocare.translate.core.model.Balance
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Tier, metering, budget and the Start routing table (CONTRACT §5), driven by
 * the same balance payloads the backend actually returns.
 */
class EntitlementStoreTest {

    private val json = Json { ignoreUnknownKeys = true }
    private fun balance(name: String): Balance = json.decodeFromString(Fixtures.read(name))
    private fun snapshot(name: String?, offlinePref: Boolean? = null) =
        EntitlementStore.Snapshot(name?.let(::balance), offlinePref)

    @Test
    fun `before any balance arrives nothing is cloud-allowed`() {
        val fresh = EntitlementStore().snapshot
        assertEquals(Tier.FREE, fresh.tier)
        assertTrue("a missing balance must be treated as metered", fresh.metered)
        assertFalse(fresh.cloudAllowed)
        assertTrue("an unknown tier is free, and free is ad-supported (a Pro may see one ad before sync)", fresh.showAds)
        assertTrue("free defaults to on-device", fresh.offlineMode)
    }

    @Test
    fun `a metered free tier is not cloud-allowed and sees ads`() {
        val free = snapshot("balance_free_enforced.json")
        assertEquals(Tier.FREE, free.tier)
        assertTrue(free.metered)
        assertFalse(free.cloudAllowed)
        assertTrue(free.showAds)
        assertEquals(0.0, free.budgetSeconds, 0.0)
    }

    @Test
    fun `enforced false opens the cloud to everyone but does not stop the ads`() {
        val unenforced = snapshot("balance_free_unenforced.json")
        assertFalse("explicit enforced:false is the only thing that switches metering off", unenforced.metered)
        assertTrue(unenforced.cloudAllowed)
        assertTrue("with metering off everyone is free, and everyone is ad-supported", unenforced.showAds)
        assertEquals(Double.POSITIVE_INFINITY, unenforced.budgetSeconds, 0.0)
    }

    // ── Ads (tier × build flag) ──────────────────────────────────────────────

    @Test
    fun `ads show to free and never to pro`() {
        assertTrue(EntitlementStore.Snapshot(balance("balance_free_enforced.json"), null, adsEnabled = true).showAds)
        assertTrue(EntitlementStore.Snapshot(balance("balance_free_unenforced.json"), null, adsEnabled = true).showAds)
        assertTrue("no balance yet: free", EntitlementStore.Snapshot(null, null, adsEnabled = true).showAds)
        assertFalse(EntitlementStore.Snapshot(balance("balance_pro.json"), null, adsEnabled = true).showAds)
        assertFalse(EntitlementStore.Snapshot(balance("balance_sandbox.json"), null, adsEnabled = true).showAds)
        assertFalse(
            "an exhausted pro is still pro",
            EntitlementStore.Snapshot(balance("balance_pro_exhausted.json"), null, adsEnabled = true).showAds,
        )
    }

    @Test
    fun `a build without ads shows none to anyone`() {
        assertFalse(EntitlementStore.Snapshot(balance("balance_free_enforced.json"), null, adsEnabled = false).showAds)
        assertFalse(EntitlementStore.Snapshot(null, null, adsEnabled = false).showAds)
        assertFalse(EntitlementStore.Snapshot(balance("balance_pro.json"), null, adsEnabled = false).showAds)
    }

    @Test
    fun `this build ships ad-supported — the ads flag defaults on`() {
        assertTrue("vocare.ads defaults to true", com.vocare.translate.app.BuildConfig.ADS_ENABLED)
        assertTrue(snapshot("balance_free_enforced.json").showAds)
        assertFalse(snapshot("balance_pro.json").showAds)
    }

    @Test
    fun `a missing enforced field means enforced`() {
        val missing = EntitlementStore.Snapshot(Balance(tier = "free", enforced = null), null)
        assertTrue(missing.metered)
        assertFalse(missing.cloudAllowed)
    }

    @Test
    fun `pro derives its budget from seconds_remaining`() {
        val pro = snapshot("balance_pro.json")
        assertEquals(Tier.PRO, pro.tier)
        assertTrue(pro.cloudAllowed)
        assertEquals(2700.0, pro.budgetSeconds, 0.0)
        assertFalse("pro never sees ads", pro.showAds)
        assertFalse("pro defaults to the cloud", pro.offlineMode)
    }

    @Test
    fun `a sandbox pro balance is still pro`() {
        val sandbox = snapshot("balance_sandbox.json")
        assertEquals(Tier.PRO, sandbox.tier)
        assertTrue(sandbox.balance!!.sandboxOk)
        assertFalse("a sandbox grant is not durable", sandbox.balance!!.durable)
        assertTrue(sandbox.cloudAllowed)
        assertEquals(3600.0, sandbox.budgetSeconds, 0.0)
    }

    @Test
    fun `the offline switch is tri-state and sticks once touched`() {
        assertTrue("untouched, free follows its default", snapshot("balance_free_enforced.json").offlineMode)
        assertFalse("untouched, pro follows its default", snapshot("balance_pro.json").offlineMode)
        assertFalse("a free user who turned it off keeps it off", snapshot("balance_free_enforced.json", false).offlineMode)
        assertTrue("a pro user who turned it on keeps it on", snapshot("balance_pro.json", true).offlineMode)
    }

    @Test
    fun `the upgrade chip picks the honest paywall variant`() {
        assertEquals(PaywallReason.UPSELL, snapshot("balance_free_enforced.json").upgradeReason)
        assertEquals(PaywallReason.UPSELL, snapshot("balance_pro.json").upgradeReason)
        assertEquals(PaywallReason.EXHAUSTED, snapshot("balance_pro_exhausted.json").upgradeReason)
    }

    // ── The paid surface (build flag × metering) ─────────────────────────────

    @Test
    fun `the free build has no paid surface whatever the backend says`() {
        assertFalse(
            "enforced:false must never show a paywall affordance",
            EntitlementStore.Snapshot(balance("balance_free_unenforced.json"), null, paidTierEnabled = true)
                .paidSurfaceVisible,
        )
        assertFalse(
            "even a metered backend must not surface a subscription in a build that cannot sell one",
            EntitlementStore.Snapshot(balance("balance_free_enforced.json"), null, paidTierEnabled = false)
                .paidSurfaceVisible,
        )
        assertFalse(
            EntitlementStore.Snapshot(balance("balance_pro.json"), null, paidTierEnabled = false).paidSurfaceVisible,
        )
    }

    @Test
    fun `a paid build with a metering backend shows the paid surface`() {
        assertTrue(
            EntitlementStore.Snapshot(balance("balance_free_enforced.json"), null, paidTierEnabled = true)
                .paidSurfaceVisible,
        )
        assertTrue(
            EntitlementStore.Snapshot(balance("balance_pro.json"), null, paidTierEnabled = true).paidSurfaceVisible,
        )
    }

    @Test
    fun `this build ships free — the flag defaults off, so nothing can surface a purchase`() {
        assertFalse(
            "flip vocare.paidTier only together with the Play Console product",
            com.vocare.translate.app.BuildConfig.PAID_TIER_ENABLED,
        )
        assertFalse(snapshot("balance_free_enforced.json").paidSurfaceVisible)
        assertFalse(snapshot("balance_pro.json").paidSurfaceVisible)
    }

    // ── Start routing (CONTRACT §5) ──────────────────────────────────────────

    @Test
    fun `offline switch on with a capable pair goes on-device even for pro`() {
        assertEquals(StartRoute.Offline, snapshot("balance_pro.json", true).route(pairOfflineCapable = true))
        assertEquals(StartRoute.Offline, snapshot("balance_free_enforced.json").route(pairOfflineCapable = true))
    }

    @Test
    fun `a free user on an offline-incapable pair is sent to the unsupported paywall`() {
        val route = snapshot("balance_free_enforced.json").route(pairOfflineCapable = false)
        assertEquals(StartRoute.Paywall(PaywallReason.UNSUPPORTED), route)
    }

    @Test
    fun `an unmetered backend lets a free user into the cloud when the switch is off`() {
        val route = snapshot("balance_free_unenforced.json", offlinePref = false).route(pairOfflineCapable = true)
        assertEquals(StartRoute.Cloud, route)
    }

    @Test
    fun `a spent pro balance is sent to the exhausted paywall`() {
        val route = snapshot("balance_pro_exhausted.json", offlinePref = false).route(pairOfflineCapable = false)
        assertEquals(StartRoute.Paywall(PaywallReason.EXHAUSTED), route)
    }

    @Test
    fun `a pro user with minutes left starts a cloud session`() {
        assertEquals(StartRoute.Cloud, snapshot("balance_pro.json").route(pairOfflineCapable = true))
    }

    @Test
    fun `the store carries the last balance and the preference through`() {
        val store = EntitlementStore()
        store.update(balance("balance_pro.json"))
        store.setOfflinePref(true)
        assertEquals(Tier.PRO, store.snapshot.tier)
        assertTrue(store.snapshot.offlineMode)
        assertEquals(balance("balance_pro.json"), store.balance.value)
        assertEquals(true, store.offlinePref.value)
    }
}
