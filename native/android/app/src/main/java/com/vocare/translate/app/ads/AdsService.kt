package com.vocare.translate.app.ads

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * The banner seam. The free build does not link the AdMob SDK at all — the
 * published privacy policy says the app contains "no ad networks, no analytics
 * or tracking SDKs of any kind", and Play's automated SDK scan checks the
 * binary against that claim — so the implementation is chosen by source set:
 *
 *  - `src/free/java`  → [NoAdsService], no dependency on play-services-ads.
 *  - `src/paid/java`  → the real AdMob banner.
 *
 * `app/build.gradle.kts` adds exactly one of those directories, and the AdMob
 * dependency only with the paid one. Which build ran is `BuildConfig.PAID_TIER_ENABLED`.
 *
 * Ads are never shown to pro and never on a live screen — that gate is in the
 * navigation host, not here.
 */
interface AdsService {
    /** False whenever no banner can be shown: the free build, or no configured unit id. */
    val enabled: Boolean

    /** Idempotent; a no-op when [enabled] is false. */
    fun initialise()

    @Composable
    fun Banner(modifier: Modifier = Modifier)
}

