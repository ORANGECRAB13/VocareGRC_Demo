package com.vocare.translate.app

import android.content.Context
import com.vocare.translate.app.ads.AdsService
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
     * Null when Play Billing is not present on the device (a bare emulator
     * image, a sideloaded build). [PlayPurchasesService] treats that as "no
     * store" rather than crashing the app on launch.
     */
    val storeClient: StoreClient? by lazy {
        runCatching { PlayStoreClient(app) as StoreClient }.getOrNull()
    }

    val purchases: PlayPurchasesService by lazy {
        PlayPurchasesService(storeClient, api) { preferences.clientId() }
    }

    /** Never initialises the ads SDK without a configured unit id (CONTRACT §5). */
    val ads: AdsService by lazy { AdsService(app, BuildConfig.VOCARE_ADMOB_BANNER_UNIT_ID) }

    val offlineEngine: OfflineEngine by lazy { AndroidOfflineEngine(app) }

    /**
     * libwebrtc initialisation opens the audio device, so it is deferred until a
     * cloud session actually starts (and after RECORD_AUDIO was granted).
     */
    fun legFactory(): PeerLegFactory = webRtc ?: WebRtcLegFactory(app).also { webRtc = it }

    private var webRtc: PeerLegFactory? = null
}
