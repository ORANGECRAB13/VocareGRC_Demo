package com.vocare.translate.app

import android.content.Context
import com.vocare.translate.app.ads.AdsRuntime
import com.vocare.translate.app.ads.adsRuntime
import com.vocare.translate.app.api.RetrofitVocaApi
import com.vocare.translate.app.api.VocaAccountApi
import com.vocare.translate.app.history.HistoryRepository
import com.vocare.translate.app.store.EntitlementStore
import com.vocare.translate.app.store.PlayPurchasesService
import com.vocare.translate.app.store.PlayStoreClient
import com.vocare.translate.app.store.StoreClient
import com.vocare.translate.app.store.VocaPreferences
import com.vocare.translate.core.offline.OfflineEngine
import com.vocare.translate.session.cloud.PeerLegFactory
import com.vocare.translate.session.cloud.WebRtcLegFactory
import com.vocare.translate.session.offline.AndroidOfflineEngine

/**
 * Manual dependency injection. Everything the app needs is built once, lazily,
 * from the application context — no Hilt, no reflection, and every collaborator
 * is an interface so tests construct their own container-free objects.
 */
class AppContainer(context: Context) {
    private val app = context.applicationContext

    val preferences: VocaPreferences by lazy { VocaPreferences(app) }

    val api: VocaAccountApi by lazy { RetrofitVocaApi(BuildConfig.VOCARE_API_BASE) }

    val history: HistoryRepository by lazy {
        HistoryRepository(
            dao = HistoryRepository.openDatabase(app).sessionDao(),
            api = api,
            clientId = { preferences.clientId() },
        )
    }

    val entitlements: EntitlementStore by lazy { EntitlementStore() }

    /**
     * Null when the paid tier is switched off at build time, and null when Play
     * Billing is not present on the device (a bare emulator image, a sideloaded
     * build). [PlayPurchasesService] treats either as "no store" rather than
     * crashing the app on launch.
     *
     * The build-flag check comes first on purpose: a free build declares no
     * `com.android.vending.BILLING` permission, so a BillingClient must never be
     * constructed — it would only log errors on a user's device.
     */
    val storeClient: StoreClient? by lazy {
        if (!BuildConfig.PAID_TIER_ENABLED) null else runCatching { PlayStoreClient(app) as StoreClient }.getOrNull()
    }

    val purchases: PlayPurchasesService by lazy {
        PlayPurchasesService(storeClient, api) { preferences.clientId() }
    }

    /**
     * AdMob + UMP behind an SDK-free seam; `NoAdsRuntime` in a build with
     * `vocare.ads=false`. Nothing initialises until consent allows it, and the
     * App Open manager never shows over a live session (CONTRACT §5).
     */
    val ads: AdsRuntime by lazy { adsRuntime(app) }

    val offlineEngine: OfflineEngine by lazy { AndroidOfflineEngine(app) }

    /**
     * libwebrtc initialisation opens the audio device, so it is deferred until a
     * cloud session actually starts (and after RECORD_AUDIO was granted).
     */
    fun legFactory(): PeerLegFactory = webRtc ?: WebRtcLegFactory(app).also { webRtc = it }

    private var webRtc: PeerLegFactory? = null
}
