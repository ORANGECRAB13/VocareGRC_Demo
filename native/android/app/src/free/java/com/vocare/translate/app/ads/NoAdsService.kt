package com.vocare.translate.app.ads

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * The free build's banner: there isn't one. play-services-ads is not on the
 * classpath here, so no advertising SDK exists in the shipped binary and the
 * privacy policy's "no ad networks of any kind" is literally true.
 */
internal object NoAdsService : AdsService {
    override val enabled: Boolean = false
    override fun initialise() = Unit

    @Composable
    override fun Banner(modifier: Modifier) = Unit
}

/** Source-set-selected factory; see [AdsService]. */
fun adsService(context: Context, bannerUnitId: String): AdsService = NoAdsService
