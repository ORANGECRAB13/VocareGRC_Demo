package com.vocare.translate.app.ads.sdk

import android.app.Activity
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.google.android.gms.ads.AdError
import com.google.android.gms.ads.AdRequest
import com.google.android.gms.ads.AdSize
import com.google.android.gms.ads.AdView
import com.google.android.gms.ads.FullScreenContentCallback
import com.google.android.gms.ads.LoadAdError
import com.google.android.gms.ads.MobileAds
import com.google.android.gms.ads.RequestConfiguration
import com.google.android.gms.ads.appopen.AppOpenAd
import com.google.android.ump.ConsentDebugSettings
import com.google.android.ump.ConsentInformation
import com.google.android.ump.ConsentRequestParameters
import com.google.android.ump.UserMessagingPlatform
import com.vocare.translate.app.ads.AdsConfig
import com.vocare.translate.app.ads.AdsRuntime
import com.vocare.translate.app.ads.AppOpenAdLoader
import com.vocare.translate.app.ads.AppOpenAdManager
import com.vocare.translate.app.ads.AppOpenAds
import com.vocare.translate.app.ads.LoadedAppOpenAd
import com.vocare.translate.app.ui.Routes
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "VocaAds"

/**
 * The only class that touches AdMob and the User Messaging Platform. It is
 * instantiated by name from `ads/AdsRuntime.kt` (keep rule in
 * proguard-rules.pro) and excluded from the build when `vocare.ads=false`.
 *
 * Startup order, as Google requires for EEA/UK serving:
 *  1. `requestConsentInfoUpdate` on every launch;
 *  2. `loadAndShowConsentFormIfRequired` — the consent message is mandatory
 *     where it applies and is never skipped;
 *  3. only once `canRequestAds()` — `MobileAds.initialize` (off the main
 *     thread) and the App Open preload.
 */
class AdMobRuntime(context: Context, private val config: AdsConfig) : AdsRuntime {
    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val consent: ConsentInformation = UserMessagingPlatform.getConsentInformation(app)
    private val sdkStarted = AtomicBoolean(false)

    private val manager = AppOpenAdManager(
        loader = AdMobAppOpenLoader(app),
        unitId = config.appOpenUnitId,
        clock = System::currentTimeMillis,
        schedule = { delayMs, action -> main.postDelayed(action, delayMs) },
        foregroundCapMinutes = config.foregroundCapMinutes,
        coldStartRoutes = setOf(Routes.HOME),
        blockedRoutes = setOf(Routes.LIVE_CLOUD, Routes.LIVE_OFFLINE, Routes.PAYWALL, Routes.ERROR),
    )

    override val appOpen: AppOpenAds get() = manager

    override fun start(activity: Activity) {
        val params = ConsentRequestParameters.Builder().apply {
            // Debug geography lets the EEA message be exercised from anywhere,
            // but only on a registered test device and only in a debug build:
            // ConsentDebugSettings is silently ignored by the SDK otherwise,
            // and we never ship it.
            if (config.debugBuild && config.umpTestDeviceHash.isNotBlank()) {
                setConsentDebugSettings(
                    ConsentDebugSettings.Builder(activity)
                        .setDebugGeography(ConsentDebugSettings.DebugGeography.DEBUG_GEOGRAPHY_EEA)
                        .addTestDeviceHashedId(config.umpTestDeviceHash)
                        .build(),
                )
            }
        }.build()

        consent.requestConsentInfoUpdate(
            activity,
            params,
            {
                UserMessagingPlatform.loadAndShowConsentFormIfRequired(activity) { formError ->
                    if (formError != null) Log.w(TAG, "consent form: ${formError.errorCode} ${formError.message}")
                    startSdkIfPermitted()
                }
            },
            { requestError ->
                Log.w(TAG, "consent update: ${requestError.errorCode} ${requestError.message}")
                // A failed update leaves a previous launch's answer in force.
                startSdkIfPermitted()
            },
        )
        // A returning user has usually already answered; do not make them wait
        // for the network round-trip above before the preload begins.
        startSdkIfPermitted()
    }

    private fun startSdkIfPermitted() {
        if (!consent.canRequestAds()) return
        if (!sdkStarted.compareAndSet(false, true)) return
        if (config.testDeviceIds.isNotEmpty()) {
            MobileAds.setRequestConfiguration(
                RequestConfiguration.Builder().setTestDeviceIds(config.testDeviceIds).build(),
            )
        }
        Thread {
            MobileAds.initialize(app) { main.post { manager.onConsentResolved(true) } }
        }.apply { name = "voca-ads-init" }.start()
    }

    override val privacyOptionsRequired: Boolean
        get() = consent.privacyOptionsRequirementStatus ==
            ConsentInformation.PrivacyOptionsRequirementStatus.REQUIRED

    override fun showPrivacyOptions(activity: Activity) {
        if (!privacyOptionsRequired) return
        UserMessagingPlatform.showPrivacyOptionsForm(activity) { formError ->
            if (formError != null) Log.w(TAG, "privacy options: ${formError.errorCode} ${formError.message}")
        }
    }

    @Composable
    override fun Banner(modifier: Modifier) {
        if (config.bannerUnitId.isBlank() || !sdkStarted.get()) return
        AndroidView(
            modifier = modifier.fillMaxWidth(),
            factory = { ctx ->
                AdView(ctx).apply {
                    setAdSize(AdSize.BANNER)
                    adUnitId = config.bannerUnitId
                    loadAd(AdRequest.Builder().build())
                }
            },
        )
    }
}

/**
 * [AppOpenAdLoader] over `AppOpenAd.load`. Orientation is not passed: the SDK
 * derives it from the activity, and MainActivity is locked to portrait.
 */
internal class AdMobAppOpenLoader(private val app: Context) : AppOpenAdLoader {
    override fun load(unitId: String, onLoaded: (LoadedAppOpenAd) -> Unit, onFailed: (String) -> Unit) {
        AppOpenAd.load(
            app,
            unitId,
            AdRequest.Builder().build(),
            object : AppOpenAd.AppOpenAdLoadCallback() {
                override fun onAdLoaded(ad: AppOpenAd) = onLoaded(Sdk(ad))
                override fun onAdFailedToLoad(error: LoadAdError) {
                    Log.w(TAG, "app open load failed: ${error.code} ${error.message}")
                    onFailed(error.message)
                }
            },
        )
    }

    /**
     * The close affordance and any countdown are drawn by Google inside the
     * ad; nothing here overlays, hides, delays or auto-dismisses it.
     */
    private class Sdk(private val ad: AppOpenAd) : LoadedAppOpenAd {
        override fun show(activity: Activity, listener: LoadedAppOpenAd.ShowListener) {
            ad.fullScreenContentCallback = object : FullScreenContentCallback() {
                override fun onAdShowedFullScreenContent() = listener.onShown()
                override fun onAdDismissedFullScreenContent() = listener.onDismissed()
                override fun onAdFailedToShowFullScreenContent(error: AdError) {
                    Log.w(TAG, "app open show failed: ${error.code} ${error.message}")
                    listener.onFailedToShow(error.message)
                }
            }
            ad.show(activity)
        }
    }
}
