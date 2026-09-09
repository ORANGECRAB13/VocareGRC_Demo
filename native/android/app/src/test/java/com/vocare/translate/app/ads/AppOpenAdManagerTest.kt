package com.vocare.translate.app.ads

import android.app.Activity
import com.vocare.translate.app.ui.Routes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The App Open state machine under a fake loader, a fake clock and a captured
 * scheduler. Every rule the manager promises (see its class comment) has a
 * test here; none of them touch the AdMob SDK.
 */
class AppOpenAdManagerTest {

    private class FakeAd : LoadedAppOpenAd {
        var shows = 0
        var listener: LoadedAppOpenAd.ShowListener? = null
        override fun show(activity: Activity, listener: LoadedAppOpenAd.ShowListener) {
            shows += 1
            this.listener = listener
            listener.onShown()
        }

        fun dismiss() = listener!!.onDismissed()
    }

    private class FakeLoader : AppOpenAdLoader {
        var requests = 0
        private var pending: Pair<(LoadedAppOpenAd) -> Unit, (String) -> Unit>? = null
        override fun load(unitId: String, onLoaded: (LoadedAppOpenAd) -> Unit, onFailed: (String) -> Unit) {
            requests += 1
            pending = onLoaded to onFailed
        }

        fun succeed(): FakeAd = FakeAd().also { ad -> pending!!.first(ad); pending = null }
        fun fail() = pending!!.second("no fill").also { pending = null }
        val hasPending: Boolean get() = pending != null
    }

    private class Scheduled(val at: Long, val action: () -> Unit)

    private inner class Harness(capMinutes: Int = 240) {
        var now = 0L
        val loader = FakeLoader()
        val scheduled = mutableListOf<Scheduled>()
        val activity = Activity()
        val manager = AppOpenAdManager(
            loader = loader,
            unitId = "unit",
            clock = { now },
            schedule = { delay, action -> scheduled += Scheduled(now + delay, action) },
            foregroundCapMinutes = capMinutes,
            coldStartRoutes = setOf(Routes.HOME),
            blockedRoutes = setOf(Routes.LIVE_CLOUD, Routes.LIVE_OFFLINE, Routes.PAYWALL, Routes.ERROR),
        )

        /** Advance time and run everything that fell due, in order. */
        fun advance(ms: Long) {
            now += ms
            while (true) {
                val due = scheduled.filter { it.at <= now }.minByOrNull { it.at } ?: return
                scheduled.remove(due)
                due.action()
            }
        }

        /** Free user, consent given, sitting on Home: the ordinary launch. */
        fun readyOnHome(): Harness {
            manager.setAdsAllowedForUser(true)
            manager.onConsentResolved(true)
            manager.setSurface(AdSurface(route = Routes.HOME))
            return this
        }
    }

    // ── consent and tier ─────────────────────────────────────────────────────

    @Test
    fun `nothing loads until consent says ads may be requested`() {
        val h = Harness()
        h.manager.setAdsAllowedForUser(true)
        h.manager.setSurface(AdSurface(route = Routes.HOME))
        h.manager.onHomeRendered(h.activity)
        h.manager.onAppForegrounded(h.activity)
        assertEquals("no consent, no request", 0, h.loader.requests)

        h.manager.onConsentResolved(false)
        assertEquals(0, h.loader.requests)

        h.manager.onConsentResolved(true)
        assertEquals("consent unlocks the preload", 1, h.loader.requests)
    }

    @Test
    fun `a pro user never loads or sees an ad`() {
        val h = Harness()
        h.manager.onConsentResolved(true)
        h.manager.setSurface(AdSurface(route = Routes.HOME))
        h.manager.setAdsAllowedForUser(false)
        h.manager.onHomeRendered(h.activity)
        assertEquals("pro: no preload", 0, h.loader.requests)

        // Even with an ad already in hand (a free user who upgraded), pro sees nothing.
        val free = Harness().readyOnHome()
        val ad = free.loader.succeed()
        free.manager.setAdsAllowedForUser(false)
        free.manager.onHomeRendered(free.activity)
        free.advance(5 * 60 * 60 * 1000L)
        free.manager.onAppForegrounded(free.activity)
        assertEquals(0, ad.shows)
    }

    // ── cold start ───────────────────────────────────────────────────────────

    @Test
    fun `cold start shows only after home has rendered`() {
        val h = Harness().readyOnHome()
        val ad = h.loader.succeed()
        assertEquals("loaded ad must wait for Home", 0, ad.shows)

        // The launch's own ON_START is not a foreground return.
        h.manager.onAppForegrounded(h.activity)
        assertEquals(0, ad.shows)

        h.manager.onHomeRendered(h.activity)
        assertEquals(1, ad.shows)
        h.manager.onHomeRendered(h.activity)
        assertEquals("once per process", 1, ad.shows)
    }

    @Test
    fun `an ad that lands just after home rendered still shows, one that lands late does not`() {
        val h = Harness().readyOnHome()
        h.manager.onHomeRendered(h.activity)
        h.advance(1_000)
        val ad = h.loader.succeed()
        assertEquals("inside the grace window", 1, ad.shows)

        val late = Harness().readyOnHome()
        late.manager.onHomeRendered(late.activity)
        late.advance(AppOpenAdManager.COLD_START_GRACE_MS + 10)
        val lateAd = late.loader.succeed()
        assertEquals("the launch is over; the user is using the app", 0, lateAd.shows)
    }

    @Test
    fun `cold start does not show over the mic disclosure sheet or off home`() {
        val h = Harness().readyOnHome()
        val ad = h.loader.succeed()
        h.manager.setSurface(AdSurface(route = Routes.HOME, micDisclosureVisible = true))
        h.manager.onHomeRendered(h.activity)
        assertEquals(0, ad.shows)

        val elsewhere = Harness().readyOnHome()
        val ad2 = elsewhere.loader.succeed()
        elsewhere.manager.setSurface(AdSurface(route = Routes.SETTINGS))
        elsewhere.manager.onHomeRendered(elsewhere.activity)
        assertEquals("cold start covers Home only", 0, ad2.shows)
        elsewhere.manager.setSurface(AdSurface(route = Routes.PAYWALL))
        elsewhere.manager.onHomeRendered(elsewhere.activity)
        assertEquals("never over the paywall", 0, ad2.shows)
    }

    // ── live sessions ────────────────────────────────────────────────────────

    @Test
    fun `never on a live session route`() {
        val h = Harness().readyOnHome()
        val ad = h.loader.succeed()
        h.manager.onHomeRendered(h.activity)
        ad.dismiss()
        h.advance(5 * 60 * 60 * 1000L)   // past the foreground cap
        val next = h.loader.succeed()

        for (route in listOf(Routes.LIVE_CLOUD, Routes.LIVE_OFFLINE)) {
            h.manager.setSurface(AdSurface(route = route))
            h.manager.onAppForegrounded(h.activity)
            assertEquals("no ad over $route", 0, next.shows)
        }
        h.manager.setSurface(AdSurface(route = Routes.HOME))
        h.manager.onAppForegrounded(h.activity)
        assertEquals("back on Home it is fine", 1, next.shows)
    }

    @Test
    fun `never while a session is in progress in the background, whatever the route`() {
        val h = Harness().readyOnHome()
        val first = h.loader.succeed()
        h.manager.setLiveSessionActive(true)
        h.manager.onHomeRendered(h.activity)
        assertEquals("a session connecting under Home still owns the mic", 0, first.shows)
        h.advance(AppOpenAdManager.COLD_START_GRACE_MS + 10)   // the launch window closes
        h.manager.setLiveSessionActive(false)

        // Later: the user starts a session, backgrounds the app mid-call, comes back.
        h.manager.setLiveSessionActive(true)
        h.manager.setSurface(AdSurface(route = Routes.HOME))
        h.manager.onAppForegrounded(h.activity)
        assertEquals("microphone open in the background: no ad", 0, first.shows)

        h.manager.setLiveSessionActive(false)
        h.manager.onAppForegrounded(h.activity)
        assertEquals(1, first.shows)
    }

    // ── foreground cap ───────────────────────────────────────────────────────

    @Test
    fun `foreground shows are capped to one per four hours`() {
        val h = Harness().readyOnHome()
        val first = h.loader.succeed()
        h.manager.onHomeRendered(h.activity)
        assertEquals("t=0: the cold-start show", 1, first.shows)
        first.dismiss()

        h.advance(60 * 60 * 1000L)
        val second = h.loader.succeed()
        h.manager.onAppForegrounded(h.activity)
        assertEquals("t=+1h: capped", 0, second.shows)

        h.advance(3 * 60 * 60 * 1000L + 1)
        h.manager.onAppForegrounded(h.activity)
        assertEquals("t=+4h: allowed again", 1, second.shows)
        second.dismiss()

        val third = h.loader.succeed()
        h.manager.onAppForegrounded(h.activity)
        assertEquals("straight back in: capped again", 0, third.shows)
    }

    @Test
    fun `the cap is configurable`() {
        val h = Harness(capMinutes = 10).readyOnHome()
        val first = h.loader.succeed()
        h.manager.onHomeRendered(h.activity)
        first.dismiss()
        val second = h.loader.succeed()
        h.advance(10 * 60 * 1000L)
        h.manager.onAppForegrounded(h.activity)
        assertEquals(1, second.shows)
    }

    @Test
    fun `a foreground return shows when the cold start could not`() {
        val h = Harness().readyOnHome()
        h.manager.onHomeRendered(h.activity)          // nothing loaded yet
        h.advance(AppOpenAdManager.COLD_START_GRACE_MS + 10)
        val ad = h.loader.succeed()
        assertEquals(0, ad.shows)
        h.manager.onAppForegrounded(h.activity)
        assertEquals("t=0 for foreground shows: nothing has shown yet", 1, ad.shows)
    }

    // ── validity, reload, backoff ────────────────────────────────────────────

    @Test
    fun `an expired preloaded ad is discarded and a fresh one loaded`() {
        val h = Harness().readyOnHome()
        val stale = h.loader.succeed()
        assertTrue(h.manager.hasAd)
        h.now += AppOpenAdManager.AD_VALIDITY_MS + 1   // no scheduled work run: pure show-time check
        h.manager.onHomeRendered(h.activity)
        assertEquals("a four-hour-old ad is never shown", 0, stale.shows)
        assertFalse(h.manager.hasAd)
        assertTrue("and a replacement is requested", h.loader.hasPending)
        assertEquals(2, h.loader.requests)
    }

    @Test
    fun `an ad expires in place while idle`() {
        val h = Harness().readyOnHome()
        h.loader.succeed()
        h.advance(AppOpenAdManager.AD_VALIDITY_MS)
        assertFalse(h.manager.hasAd)
        assertEquals("expiry triggers the reload", 2, h.loader.requests)
    }

    @Test
    fun `the next ad preloads after a show is dismissed`() {
        val h = Harness().readyOnHome()
        val ad = h.loader.succeed()
        h.manager.onHomeRendered(h.activity)
        assertEquals("nothing reloads while the ad is on screen", 1, h.loader.requests)
        ad.dismiss()
        assertEquals(2, h.loader.requests)
    }

    @Test
    fun `load failure backs off instead of looping`() {
        val h = Harness().readyOnHome()
        assertEquals(1, h.loader.requests)
        h.loader.fail()
        assertEquals("no immediate retry", 1, h.loader.requests)
        assertFalse(h.manager.isLoading)

        h.advance(29_999)
        assertEquals(1, h.loader.requests)
        h.advance(1)
        assertEquals("first retry after 30 s", 2, h.loader.requests)

        h.loader.fail()
        h.advance(30_000)
        assertEquals("second retry waits 60 s", 2, h.loader.requests)
        h.advance(30_000)
        assertEquals(3, h.loader.requests)

        h.loader.succeed()
        h.manager.onHomeRendered(h.activity)
        assertEquals(30_000L, AppOpenAdManager.backoffMs(1))
        assertEquals(30 * 60_000L, AppOpenAdManager.backoffMs(11))
        assertEquals("the cap holds however many failures", 30 * 60_000L, AppOpenAdManager.backoffMs(40))
    }

    @Test
    fun `a blank unit id never calls the loader`() {
        val h = Harness()
        val manager = AppOpenAdManager(
            loader = h.loader, unitId = "", clock = { h.now }, schedule = { _, _ -> },
            coldStartRoutes = setOf(Routes.HOME), blockedRoutes = emptySet(),
        )
        manager.setAdsAllowedForUser(true)
        manager.onConsentResolved(true)
        assertEquals(0, h.loader.requests)
    }
}
