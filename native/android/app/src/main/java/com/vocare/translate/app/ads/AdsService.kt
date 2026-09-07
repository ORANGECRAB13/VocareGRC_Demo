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
 * Free-tier banner. The SDK is never initialised without a configured ad unit
 * id (CONTRACT §5): a missing id must cost us a banner, never the session
 * someone is in. Ads are never shown to pro and never on a live screen — that
 * gate is in the navigation host, not here.
 */
class AdsService(private val context: Context, private val bannerUnitId: String) {
    private val initialised = AtomicBoolean(false)

    val enabled: Boolean get() = bannerUnitId.isNotBlank()

    /** Idempotent, off the main thread is fine; no-op when no unit id is configured. */
    fun initialise() {
        if (!enabled || !initialised.compareAndSet(false, true)) return
        MobileAds.initialize(context.applicationContext) {}
    }

    @Composable
    fun Banner(modifier: Modifier = Modifier) {
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
