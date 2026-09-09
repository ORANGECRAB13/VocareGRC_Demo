package com.vocare.translate.app.ui

import com.vocare.translate.app.BuildConfig
import com.vocare.translate.app.store.PaywallReason
import com.vocare.translate.app.store.StartRoute

/**
 * The build-time kill switch for every in-app-purchase surface.
 *
 * With [ENABLED] false the shipped app is a plain free app: the paywall is not
 * registered in the nav graph at all, the tier chip is inert, Settings has no
 * subscription section, and no server response, state combination or deep link
 * can produce a Subscribe button. The billing source stays in the tree and keeps
 * compiling, so turning the paid tier on is a flag plus Play Console setup.
 *
 * The functions take the flag as a parameter so both builds are testable in one
 * test run; production callers use the default.
 */
object PaidSurface {
    /** [BuildConfig.PAID_TIER_ENABLED] — false in the free build. */
    const val ENABLED: Boolean = BuildConfig.PAID_TIER_ENABLED

    /** The paywall destination, or null when this build has no paywall to navigate to. */
    fun paywallRoute(reason: PaywallReason, enabled: Boolean = ENABLED): String? =
        if (enabled) Routes.paywall(reason) else null

    /**
     * Start routing, with the paywall taken out of the picture in a free build.
     *
     * A build that cannot sell anything must never dead-end Start on a
     * destination it does not have, so a paywall verdict becomes a cloud
     * attempt. In the free configuration this branch is unreachable anyway
     * (`enforced:false` means cloud-allowed with an infinite budget); it only
     * matters if the backend ever starts metering an already-shipped free build,
     * where a normal session error beats a Start button that does nothing.
     */
    fun resolve(route: StartRoute, enabled: Boolean = ENABLED): StartRoute =
        if (!enabled && route is StartRoute.Paywall) StartRoute.Cloud else route
}
