package com.vocare.translate.app.ads

import android.app.Activity
import android.content.Context
import android.util.Log
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.vocare.translate.app.BuildConfig

/**
 * Everything the app needs from advertising, SDK-free. The one implementation
 * that talks to AdMob and the User Messaging Platform lives in `ads/sdk/` and
 * is only compiled when the build links those SDKs (`vocare.ads`, default
 * true); [adsRuntime] finds it by name so that a build without ads has no
 * reference to either SDK anywhere.
 *
 * Gates that decide whether an ad may appear at all live elsewhere:
 *  - who sees ads: `EntitlementStore.Snapshot.showAds` (never Pro);
 *  - when an App Open ad may show: [AppOpenAdManager];
 *  - where a banner may sit: the navigation host (never on a live screen).
 */
interface AdsRuntime {
    /** The App Open ad state machine; [AppOpenAds.None] when ads are off. */
    val appOpen: AppOpenAds

    /**
     * Gather consent (UMP), then — only once `canRequestAds()` — initialise
     * the ads SDK and let [appOpen] preload. Safe to call on every launch;
     * Google asks for the consent update on every launch.
     */
    fun start(activity: Activity)

    /** True when UMP says the user must be able to revisit their choices (EEA/UK). */
    val privacyOptionsRequired: Boolean

    /** Shows the UMP privacy-options form; a no-op when [privacyOptionsRequired] is false. */
    fun showPrivacyOptions(activity: Activity)

    /** Inline banner; renders nothing without a configured banner unit id. */
    @Composable
    fun Banner(modifier: Modifier = Modifier)
}

/** The build without ads, and the fallback if the SDK runtime cannot be constructed. */
object NoAdsRuntime : AdsRuntime {
    override val appOpen: AppOpenAds get() = AppOpenAds.None
    override fun start(activity: Activity) = Unit
    override val privacyOptionsRequired: Boolean get() = false
    override fun showPrivacyOptions(activity: Activity) = Unit

    @Composable
    override fun Banner(modifier: Modifier) = Unit
}

/** Build-time configuration handed to the SDK runtime; all values come from BuildConfig. */
data class AdsConfig(
    val enabled: Boolean = BuildConfig.ADS_ENABLED,
    val appOpenUnitId: String = BuildConfig.VOCARE_ADMOB_APP_OPEN_UNIT_ID,
    val bannerUnitId: String = BuildConfig.VOCARE_ADMOB_BANNER_UNIT_ID,
    /** Comma-separated AdMob test device ids; empty in production. */
    val testDeviceIds: List<String> =
        BuildConfig.VOCARE_ADMOB_TEST_DEVICE_IDS.split(',').map { it.trim() }.filter { it.isNotEmpty() },
    /** UMP hashed device id for the EEA debug geography; only honoured in debug builds. */
    val umpTestDeviceHash: String = BuildConfig.VOCARE_UMP_TEST_DEVICE_HASH,
    val debugBuild: Boolean = BuildConfig.DEBUG,
    val foregroundCapMinutes: Int = AppOpenAdManager.DEFAULT_FOREGROUND_CAP_MINUTES,
)

private const val SDK_RUNTIME_CLASS = "com.vocare.translate.app.ads.sdk.AdMobRuntime"

/**
 * The AdMob runtime when the build has ads, [NoAdsRuntime] otherwise. Looked
 * up by name (see the class comment); a build that says ads are enabled but
 * lacks the class is a packaging bug, logged loudly and degraded to no ads
 * rather than a crash on launch.
 */
fun adsRuntime(context: Context, config: AdsConfig = AdsConfig()): AdsRuntime {
    if (!config.enabled) return NoAdsRuntime
    return runCatching {
        Class.forName(SDK_RUNTIME_CLASS)
            .getConstructor(Context::class.java, AdsConfig::class.java)
            .newInstance(context.applicationContext, config) as AdsRuntime
    }.getOrElse { error ->
        Log.e("VocaAds", "ADS_ENABLED but $SDK_RUNTIME_CLASS is unavailable; running without ads", error)
        NoAdsRuntime
    }
}
