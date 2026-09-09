package com.vocare.translate.app.store

import com.vocare.translate.app.BuildConfig
import com.vocare.translate.core.model.Balance
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** Where Start sends the user (CONTRACT §5 Routing on Start). */
sealed interface StartRoute {
    data object Offline : StartRoute
    data object Cloud : StartRoute
    data class Paywall(val reason: PaywallReason) : StartRoute
}

enum class PaywallReason { UPSELL, UNSUPPORTED, EXHAUSTED }

enum class Tier { FREE, PRO }

/**
 * Tier, metering and budget as derived from the last balance the server gave
 * us, plus the tri-state offline preference. This is pure state and routing;
 * fetching lives in [PlayPurchasesService.syncEntitlement].
 *
 * Before any balance arrives the store behaves like a metered free tier: not
 * cloud-allowed, so nothing can reach the paid engine on a guess.
 */
class EntitlementStore {
    private val _balance = MutableStateFlow<Balance?>(null)
    val balance: StateFlow<Balance?> = _balance.asStateFlow()

    private val _offlinePref = MutableStateFlow<Boolean?>(null)
    val offlinePref: StateFlow<Boolean?> = _offlinePref.asStateFlow()

    fun update(balance: Balance?) = _balance.update { balance }
    fun setOfflinePref(value: Boolean?) = _offlinePref.update { value }

    val snapshot: Snapshot get() = Snapshot.of(_balance.value, _offlinePref.value)

    /**
     * @param paidTierEnabled the build-time switch ([BuildConfig.PAID_TIER_ENABLED]).
     * @param adsEnabled the build-time ads switch ([BuildConfig.ADS_ENABLED]).
     * Both are constructor parameters rather than direct reads so every build
     * is testable from one test run.
     */
    data class Snapshot(
        val balance: Balance?,
        val offlinePref: Boolean?,
        val paidTierEnabled: Boolean = BuildConfig.PAID_TIER_ENABLED,
        val adsEnabled: Boolean = BuildConfig.ADS_ENABLED,
    ) {
        val tier: Tier get() = if (balance?.isPro == true) Tier.PRO else Tier.FREE
        val secondsLeft: Int get() = balance?.secondsRemaining ?: 0

        /** `enforced:false` (current production) is the only thing that switches metering off. */
        val metered: Boolean get() = balance?.enforced != false
        val cloudAllowed: Boolean get() = tier == Tier.PRO || !metered
        val budgetSeconds: Double get() = if (metered) secondsLeft.toDouble() else Double.POSITIVE_INFINITY

        /** Free users default to on-device; once touched the switch wins for good. */
        val offlineMode: Boolean get() = offlinePref ?: (tier != Tier.PRO)

        /**
         * The app is ad-supported: everyone who is not Pro sees ads, metering or
         * not (with metering off there is no Pro, so everyone is free). Pro never
         * does. Accepted edge: before the first entitlement sync completes a
         * returning Pro subscriber is, to this snapshot, still free, and may see
         * one App Open ad on that launch.
         */
        val showAds: Boolean get() = adsEnabled && tier == Tier.FREE

        /**
         * Whether the app may show any in-app-purchase surface at all: the
         * upgrade chip, the Settings subscription section, the paywall, the Play
         * management link. Off in the free build, and off while the backend is
         * not metering — a free app must not offer a subscription it does not
         * need and (until Play Console has the product) cannot sell.
         */
        val paidSurfaceVisible: Boolean get() = paidTierEnabled && metered

        /** The chip / Settings shortcut into the paywall picks the honest variant. */
        val upgradeReason: PaywallReason
            get() = if (tier == Tier.PRO && secondsLeft <= 0) PaywallReason.EXHAUSTED else PaywallReason.UPSELL

        fun route(pairOfflineCapable: Boolean): StartRoute = when {
            offlineMode && pairOfflineCapable -> StartRoute.Offline
            !cloudAllowed -> if (pairOfflineCapable) StartRoute.Offline else StartRoute.Paywall(PaywallReason.UNSUPPORTED)
            metered && secondsLeft <= 0 -> StartRoute.Paywall(PaywallReason.EXHAUSTED)
            else -> StartRoute.Cloud
        }

        companion object {
            fun of(balance: Balance?, offlinePref: Boolean?) = Snapshot(balance, offlinePref)
        }
    }
}
