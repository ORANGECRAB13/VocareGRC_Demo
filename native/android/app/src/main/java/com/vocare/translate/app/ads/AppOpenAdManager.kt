package com.vocare.translate.app.ads

import android.app.Activity
import java.lang.ref.WeakReference

/** What the navigation host tells the manager about the screen on top. */
data class AdSurface(
    /** The current nav route pattern (e.g. `Routes.HOME`), null before the graph is up. */
    val route: String? = null,
    /** The microphone disclosure sheet is up; nothing may cover it. */
    val micDisclosureVisible: Boolean = false,
)

/** A loaded ad the SDK handed over. [show] is one-shot. */
interface LoadedAppOpenAd {
    fun show(activity: Activity, listener: ShowListener)

    interface ShowListener {
        fun onShown()
        fun onDismissed()
        fun onFailedToShow(message: String)
    }
}

/** The SDK's load call, abstracted so the manager is testable without AdMob. */
fun interface AppOpenAdLoader {
    fun load(unitId: String, onLoaded: (LoadedAppOpenAd) -> Unit, onFailed: (message: String) -> Unit)
}

/**
 * The App Open ad surface as the rest of the app sees it. Every method is a
 * main-thread call; none of them block, throw, or touch the SDK when ads are
 * not allowed.
 */
interface AppOpenAds {
    /** From the consent flow. Nothing loads until this has said `true`. */
    fun onConsentResolved(canRequestAds: Boolean)

    /** `EntitlementStore.Snapshot.showAds`: false for Pro (and for a build without ads). */
    fun setAdsAllowedForUser(allowed: Boolean)

    /** A cloud or offline session is connecting or live — foreground or not. */
    fun setLiveSessionActive(active: Boolean)

    fun setSurface(surface: AdSurface)

    /** ProcessLifecycleOwner ON_START. The first one of a process is the launch, not a return. */
    fun onAppForegrounded(activity: Activity?)

    /** Home has composed its first frame (cold start). Idempotent per process. */
    fun onHomeRendered(activity: Activity)

    object None : AppOpenAds {
        override fun onConsentResolved(canRequestAds: Boolean) = Unit
        override fun setAdsAllowedForUser(allowed: Boolean) = Unit
        override fun setLiveSessionActive(active: Boolean) = Unit
        override fun setSurface(surface: AdSurface) = Unit
        override fun onAppForegrounded(activity: Activity?) = Unit
        override fun onHomeRendered(activity: Activity) = Unit
    }
}

/**
 * The App Open ad state machine. Pure logic: the SDK is behind [AppOpenAdLoader],
 * time behind [clock], and delayed work behind [schedule], so the whole thing
 * runs under JUnit with fakes.
 *
 * What it guarantees:
 *  - nothing loads until consent says ads may be requested, and never for Pro;
 *  - an ad shows at most once on cold start, and only after Home has rendered
 *    (never over a loading state, the mic disclosure sheet, or the paywall);
 *  - on a foreground return, at most one show per [foregroundCapMinutes];
 *  - never while a live session is connecting or live, on screen or in the
 *    background — an ad over a translation with the microphone open is not
 *    acceptable;
 *  - a preloaded ad older than [adValidityMs] (AdMob's stated four hours) is
 *    discarded and replaced, never shown;
 *  - a failed load retries with exponential backoff (30 s doubling to 30 min),
 *    never in a hot loop;
 *  - after every show, or show failure, the next ad preloads.
 *
 * Play / AdMob compliance: the close control and any countdown on an App Open
 * ad are rendered by Google inside the ad. This code never overlays a timer,
 * blocks, obscures or delays that control, and never auto-dismisses the ad —
 * the user closes it, and only then does the app continue.
 */
class AppOpenAdManager(
    private val loader: AppOpenAdLoader,
    private val unitId: String,
    private val clock: () -> Long,
    private val schedule: (delayMs: Long, action: () -> Unit) -> Unit,
    private val foregroundCapMinutes: Int = DEFAULT_FOREGROUND_CAP_MINUTES,
    private val adValidityMs: Long = AD_VALIDITY_MS,
    private val coldStartGraceMs: Long = COLD_START_GRACE_MS,
    /** Routes a cold-start show may cover: Home only. */
    private val coldStartRoutes: Set<String>,
    /** Routes nothing may ever cover: live sessions, the paywall, the error screen. */
    private val blockedRoutes: Set<String>,
) : AppOpenAds {

    private class Loaded(val ad: LoadedAppOpenAd, val loadedAt: Long)

    private enum class ColdStart { AWAITING_HOME, PENDING, DONE }

    private var canRequestAds = false
    private var adsAllowedForUser = false
    private var liveSessionActive = false
    private var surface = AdSurface()

    private var loaded: Loaded? = null
    private var loading = false
    private var consecutiveFailures = 0
    private var showing = false
    private var lastShownAt: Long? = null

    private var coldStart = ColdStart.AWAITING_HOME
    private var coldStartDeadline = 0L
    private var coldStartActivity: WeakReference<Activity>? = null

    /** Visible for tests: is a load request outstanding. */
    val isLoading: Boolean get() = loading

    /** Visible for tests: is an unexpired ad in hand. */
    val hasAd: Boolean get() = loaded != null

    // ── inputs ───────────────────────────────────────────────────────────────

    override fun onConsentResolved(canRequestAds: Boolean) {
        this.canRequestAds = canRequestAds
        if (canRequestAds) {
            ensureLoading()
            pumpColdStart()
        }
    }

    override fun setAdsAllowedForUser(allowed: Boolean) {
        adsAllowedForUser = allowed
        if (allowed) {
            ensureLoading()
            pumpColdStart()
        }
    }

    override fun setLiveSessionActive(active: Boolean) {
        liveSessionActive = active
    }

    override fun setSurface(surface: AdSurface) {
        this.surface = surface
        pumpColdStart()
    }

    override fun onAppForegrounded(activity: Activity?) {
        // The launch's own ON_START: the cold-start path (Home rendered) owns
        // that show. Only later returns from the background count here.
        if (coldStart != ColdStart.DONE) return
        if (activity == null) return
        tryShow(activity, Trigger.FOREGROUND)
    }

    override fun onHomeRendered(activity: Activity) {
        if (coldStart == ColdStart.DONE) return
        if (tryShow(activity, Trigger.COLD_START)) {
            coldStart = ColdStart.DONE
            return
        }
        // Consent and the preload usually land a moment after the first frame;
        // give them a short window, after which the launch is an ordinary one.
        if (coldStart == ColdStart.AWAITING_HOME) {
            coldStart = ColdStart.PENDING
            coldStartDeadline = clock() + coldStartGraceMs
            coldStartActivity = WeakReference(activity)
            schedule(coldStartGraceMs + 1) { if (coldStart == ColdStart.PENDING) coldStart = ColdStart.DONE }
        }
    }

    // ── machine ──────────────────────────────────────────────────────────────

    private enum class Trigger { COLD_START, FOREGROUND }

    private fun pumpColdStart() {
        if (coldStart != ColdStart.PENDING) return
        if (clock() > coldStartDeadline) {
            coldStart = ColdStart.DONE
            return
        }
        val activity = coldStartActivity?.get() ?: return
        if (tryShow(activity, Trigger.COLD_START)) coldStart = ColdStart.DONE
    }

    /** True when an ad was handed to the SDK to show. */
    private fun tryShow(activity: Activity, trigger: Trigger): Boolean {
        if (showing) return false
        if (!canRequestAds || !adsAllowedForUser) return false
        if (liveSessionActive || surface.micDisclosureVisible) return false
        val route = surface.route ?: return false
        if (route in blockedRoutes) return false
        if (trigger == Trigger.COLD_START && route !in coldStartRoutes) return false
        val now = clock()
        if (trigger == Trigger.FOREGROUND) {
            val last = lastShownAt
            if (last != null && now - last < foregroundCapMinutes * 60_000L) return false
        }
        val candidate = loaded ?: run {
            ensureLoading()
            return false
        }
        if (now - candidate.loadedAt >= adValidityMs) {
            loaded = null
            ensureLoading()
            return false
        }
        loaded = null
        showing = true
        lastShownAt = now
        candidate.ad.show(
            activity,
            object : LoadedAppOpenAd.ShowListener {
                override fun onShown() = Unit
                override fun onDismissed() = afterShow()
                override fun onFailedToShow(message: String) = afterShow()
            },
        )
        return true
    }

    private fun afterShow() {
        showing = false
        ensureLoading()
    }

    private fun ensureLoading() {
        if (!canRequestAds || !adsAllowedForUser) return
        if (loading || loaded != null || unitId.isBlank()) return
        loading = true
        loader.load(
            unitId,
            onLoaded = { ad ->
                loading = false
                consecutiveFailures = 0
                val at = clock()
                loaded = Loaded(ad, at)
                // Expire it in place so the next show never reaches for a stale ad.
                schedule(adValidityMs) {
                    if (loaded?.loadedAt == at) {
                        loaded = null
                        ensureLoading()
                    }
                }
                pumpColdStart()
            },
            onFailed = {
                loading = false
                consecutiveFailures += 1
                schedule(backoffMs(consecutiveFailures)) { ensureLoading() }
            },
        )
    }

    companion object {
        const val DEFAULT_FOREGROUND_CAP_MINUTES = 240
        const val AD_VALIDITY_MS = 4 * 60 * 60 * 1000L
        const val COLD_START_GRACE_MS = 4_000L
        private const val BACKOFF_BASE_MS = 30_000L
        private const val BACKOFF_MAX_MS = 30 * 60 * 1000L

        /** 30 s, 60 s, 2 min, … capped at 30 min. */
        fun backoffMs(failures: Int): Long {
            val shift = (failures - 1).coerceIn(0, 20)
            return (BACKOFF_BASE_MS shl shift).coerceAtMost(BACKOFF_MAX_MS)
        }
    }
}
