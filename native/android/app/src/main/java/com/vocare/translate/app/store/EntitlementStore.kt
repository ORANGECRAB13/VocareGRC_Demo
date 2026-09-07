package com.vocare.translate.app.store

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

    data class Snapshot(val balance: Balance?, val offlinePref: Boolean?) {
        val tier: Tier get() = if (balance?.isPro == true) Tier.PRO else Tier.FREE
        val secondsLeft: Int get() = balance?.secondsRemaining ?: 0

        /** `enforced:false` (current production) is the only thing that switches metering off. */
        val metered: Boolean get() = balance?.enforced != false
        val cloudAllowed: Boolean get() = tier == Tier.PRO || !metered
        val budgetSeconds: Double get() = if (metered) secondsLeft.toDouble() else Double.POSITIVE_INFINITY

        /** Free users default to on-device; once touched the switch wins for good. */
        val offlineMode: Boolean get() = offlinePref ?: (tier != Tier.PRO)

        /** Show a banner only to free users while metering exists, and only once a balance has been read. */
        val showAds: Boolean get() = balance != null && tier == Tier.FREE && metered

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
