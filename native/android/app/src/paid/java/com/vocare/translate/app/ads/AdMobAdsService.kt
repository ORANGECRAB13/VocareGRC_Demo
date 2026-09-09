package com.vocare.translate.app.ads

import android.content.Context
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.google.android.gms.ads.AdRequest
import com.google.android.gms.ads.AdSize
import com.google.android.gms.ads.AdView
import com.google.android.gms.ads.MobileAds
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The paid build's banner. Only compiled when `app/build.gradle.kts` adds
 * `src/paid/java` (and with it the play-services-ads dependency); the free
 * build uses `NoAdsService` and links no advertising SDK at all. A missing unit
 * id must cost us a banner, never the session someone is in (CONTRACT §5).
 */
internal class AdMobAdsService(private val context: Context, private val bannerUnitId: String) : AdsService {
    private val initialised = AtomicBoolean(false)

    override val enabled: Boolean get() = bannerUnitId.isNotBlank()

    /** Idempotent, off the main thread is fine; no-op when no unit id is configured. */
    override fun initialise() {
        if (!enabled || !initialised.compareAndSet(false, true)) return
        MobileAds.initialize(context.applicationContext) {}
    }

    @Composable
    override fun Banner(modifier: Modifier) {
        if (!enabled) return
        initialise()
        AndroidView(
            modifier = modifier.fillMaxWidth(),
            factory = { ctx ->
                AdView(ctx).apply {
                    setAdSize(AdSize.BANNER)
                    adUnitId = bannerUnitId
                    loadAd(AdRequest.Builder().build())
                }
            },
        )
    }
}

/** Source-set-selected factory; see [AdsService]. */
fun adsService(context: Context, bannerUnitId: String): AdsService = AdMobAdsService(context, bannerUnitId)
