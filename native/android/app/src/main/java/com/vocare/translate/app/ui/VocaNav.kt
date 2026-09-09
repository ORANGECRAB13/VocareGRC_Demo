package com.vocare.translate.app.ui

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.vocare.translate.app.MicPermissionBridge
import com.vocare.translate.app.ads.AdSurface
import com.vocare.translate.app.ads.AdsRuntime
import com.vocare.translate.app.ads.NoAdsRuntime
import com.vocare.translate.app.store.PaywallReason
import com.vocare.translate.app.store.StartRoute
import com.vocare.translate.app.store.Tier
import com.vocare.translate.core.model.SessionError
import com.vocare.translate.core.model.SessionPhase
import com.vocare.translate.core.model.Side
import com.vocare.translate.session.EndReason
import com.vocare.translate.session.cloud.MicPermission
import com.vocare.translate.session.ui.LiveSplitScreen

object Routes {
    const val HOME = "home"
    const val PICKER = "picker/{side}"
    const val PAYWALL = "paywall/{reason}"
    const val HISTORY = "history"
    const val DETAIL = "detail/{id}"
    const val SETTINGS = "settings"
    const val LIVE_CLOUD = "live/cloud"
    const val LIVE_OFFLINE = "live/offline"
    const val ERROR = "error/{kind}"

    fun picker(side: Side) = "picker/${side.name}"
    fun paywall(reason: PaywallReason) = "paywall/${reason.name}"
    fun detail(id: String) = "detail/$id"
    fun error(error: SessionError) = "error/${error.name}"
}

/**
 * The whole app's navigation (CONTRACT §5). Live screens never show a banner —
 * that gate is here, not in [AdsRuntime] — and the App Open manager is told
 * from here which route is on top, whether the mic disclosure sheet is up,
 * and whether a session is connecting or live, so it can refuse to show.
 */
@Composable
fun VocaNavHost(
    vm: AppViewModel,
    ads: AdsRuntime = NoAdsRuntime,
    navController: NavHostController = rememberNavController(),
) {
    val state by vm.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var micSheet by remember { mutableStateOf(false) }

    val banner: @Composable () -> Unit = { if (state.snapshot.showAds) ads.Banner() }

    // ── App Open ad gates ────────────────────────────────────────────────────
    val backStackEntry by navController.currentBackStackEntryAsState()
    val route = backStackEntry?.destination?.route
    LaunchedEffect(route, micSheet) {
        ads.appOpen.setSurface(AdSurface(route = route, micDisclosureVisible = micSheet))
    }
    val cloudModel by vm.cloud.collectAsStateWithLifecycle()
    val offlineModel by vm.offline.collectAsStateWithLifecycle()
    val cloudPhase = cloudModel?.state?.collectAsStateWithLifecycle()?.value?.phase
    val offlinePhase = offlineModel?.state?.collectAsStateWithLifecycle()?.value?.phase
    val liveSessionActive = cloudPhase.inProgress() || offlinePhase.inProgress()
    LaunchedEffect(liveSessionActive) { ads.appOpen.setLiveSessionActive(liveSessionActive) }

    /** Play's subscription page for this product; Play policy forbids obstructing cancellation. */
    fun openPlaySubscriptions() = runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(vm.manageSubscriptionUrl(context.packageName))),
        )
    }

    /** No-op in a build with no paywall destination. */
    fun openPaywall(reason: PaywallReason) {
        PaidSurface.paywallRoute(reason)?.let(navController::navigate)
    }

    fun go(requested: StartRoute) = when (val route = PaidSurface.resolve(requested)) {
        StartRoute.Offline -> {
            vm.beginOffline()
            navController.navigate(Routes.LIVE_OFFLINE)
        }
        StartRoute.Cloud -> {
            vm.beginCloud(MicPermission { MicPermissionBridge.request() })
            navController.navigate(Routes.LIVE_CLOUD)
        }
        is StartRoute.Paywall -> openPaywall(route.reason)
    }

    fun start() {
        if (!state.micConsentGiven) {
            micSheet = true
            return
        }
        go(vm.startRoute())
    }

    if (micSheet) {
        MicDisclosureSheet(
            onContinue = {
                micSheet = false
                vm.recordMicConsent()
                go(vm.startRoute())
            },
            onDismiss = { micSheet = false },
        )
    }

    NavHost(navController = navController, startDestination = Routes.HOME) {
        composable(Routes.HOME) {
            // Cold-start App Open trigger: Home has composed, so there is a real
            // screen for the ad to return to (never a splash or loading state).
            LaunchedEffect(Unit) { (context as? Activity)?.let(ads.appOpen::onHomeRendered) }
            HomeScreen(
                state = state,
                onStart = ::start,
                onPickLanguage = { navController.navigate(Routes.picker(it)) },
                onSwapLanguages = vm::swapLanguages,
                onOfflineToggle = vm::setOfflineMode,
                onOpenHistory = { navController.navigate(Routes.HISTORY) },
                onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                // Null in a free build: the chip stays, the upsell affordance does not.
                onOpenPaywall = if (state.snapshot.paidSurfaceVisible) {
                    { openPaywall(state.snapshot.upgradeReason) }
                } else {
                    null
                },
                banner = banner,
            )
        }

        composable(Routes.PICKER) { entry ->
            val side = Side.valueOf(entry.arguments?.getString("side") ?: Side.A.name)
            LanguagePickerScreen(
                title = if (side == Side.A) "Your language" else "Their language",
                selected = if (side == Side.A) state.langA else state.langB,
                onPick = {
                    vm.setLanguage(side, it)
                    navController.popBackStack()
                },
                onBack = { navController.popBackStack() },
            )
        }

        if (PaidSurface.ENABLED) composable(Routes.PAYWALL) { entry ->
            val reason = runCatching {
                PaywallReason.valueOf(entry.arguments?.getString("reason") ?: PaywallReason.UPSELL.name)
            }.getOrDefault(PaywallReason.UPSELL)
            val activity = context as? Activity
            PaywallScreen(
                reason = reason,
                price = state.price,
                storeAvailable = state.storeAvailable,
                busy = state.purchaseBusy,
                message = state.purchaseMessage,
                onBuy = { activity?.let(vm::buyPro) },
                onRestore = vm::restorePurchases,
                onManageInPlay = if (state.snapshot.tier == Tier.PRO) ({ openPlaySubscriptions() }) else null,
                onClose = {
                    vm.clearPurchaseMessage()
                    navController.popBackStack()
                },
            )
        }

        composable(Routes.HISTORY) {
            LaunchedEffect(Unit) { vm.loadHistory() }
            HistoryScreen(
                rows = state.history,
                loading = state.historyLoading,
                onOpen = { navController.navigate(Routes.detail(it)) },
                onBack = { navController.popBackStack() },
                banner = banner,
            )
        }

        composable(Routes.DETAIL) { entry ->
            val id = entry.arguments?.getString("id").orEmpty()
            LaunchedEffect(id) { vm.loadDetail(id) }
            SessionDetailScreen(detail = state.detail, onBack = { navController.popBackStack() })
        }

        composable(Routes.SETTINGS) {
            LaunchedEffect(Unit) { vm.refreshOfflineStatus() }
            SettingsScreen(
                state = state,
                onToggle = vm::setSetting,
                onManageSubscription = { openPaywall(state.snapshot.upgradeReason) },
                onManageInPlay = { openPlaySubscriptions() },
                onRestore = vm::restorePurchases,
                onInstall = vm::installOffline,
                onOpenLink = { url ->
                    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
                },
                // UMP: users in a region that requires it can revisit their consent.
                onPrivacyOptions = if (ads.privacyOptionsRequired) {
                    { (context as? Activity)?.let(ads::showPrivacyOptions) }
                } else {
                    null
                },
                onBack = { navController.popBackStack() },
                banner = banner,
            )
        }

        composable(Routes.LIVE_CLOUD) { CloudLiveRoute(vm, navController) }
        composable(Routes.LIVE_OFFLINE) { OfflineLiveRoute(vm, navController) }

        composable(Routes.ERROR) { entry ->
            val kind = runCatching {
                SessionError.valueOf(entry.arguments?.getString("kind") ?: SessionError.NETWORK.name)
            }.getOrDefault(SessionError.NETWORK)
            ErrorScreen(
                error = kind,
                onRetry = {
                    navController.popBackStack(Routes.HOME, inclusive = false)
                    go(vm.startRoute())
                },
                onOpenAppSettings = {
                    runCatching {
                        context.startActivity(
                            Intent(
                                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.fromParts("package", context.packageName, null),
                            ),
                        )
                    }
                },
                onHome = { navController.popBackStack(Routes.HOME, inclusive = false) },
            )
        }
    }
}

/** A session that still owns the microphone (or is about to): no ad may cover it. */
private fun SessionPhase?.inProgress(): Boolean =
    this == SessionPhase.Connecting || this == SessionPhase.Live

/** Cloud live screen: `:session`'s split view driven by [com.vocare.translate.session.cloud.CloudSessionViewModel]. */
@Composable
private fun CloudLiveRoute(vm: AppViewModel, navController: NavHostController) {
    val session by vm.cloud.collectAsStateWithLifecycle()
    val model = session ?: run { Box(Modifier.fillMaxSize()); return }
    val liveState by model.state.collectAsStateWithLifecycle()
    val endReason by model.endReason.collectAsStateWithLifecycle()

    LaunchedEffect(liveState.phase, endReason) {
        when (val phase = liveState.phase) {
            is com.vocare.translate.core.model.SessionPhase.Error -> {
                navController.popBackStack(Routes.HOME, inclusive = false)
                navController.navigate(Routes.error(phase.error))
            }
            com.vocare.translate.core.model.SessionPhase.Ended -> {
                navController.popBackStack(Routes.HOME, inclusive = false)
                vm.refreshEntitlement()
                if (endReason == EndReason.EXHAUSTED) {
                    PaidSurface.paywallRoute(PaywallReason.EXHAUSTED)?.let(navController::navigate)
                }
            }
            else -> Unit
        }
    }

    LiveSplitScreen(
        state = liveState,
        onHold = model::hold,
        onRelease = model::release,
        onEnd = model::end,
    )
}

/** On-device live screen: identical UI, [com.vocare.translate.session.offline.OfflineSessionViewModel] behind it. */
@Composable
private fun OfflineLiveRoute(vm: AppViewModel, navController: NavHostController) {
    val session by vm.offline.collectAsStateWithLifecycle()
    val model = session ?: run { Box(Modifier.fillMaxSize()); return }
    val liveState by model.state.collectAsStateWithLifecycle()

    LaunchedEffect(liveState.phase) {
        if (liveState.phase == com.vocare.translate.core.model.SessionPhase.Ended) {
            navController.popBackStack(Routes.HOME, inclusive = false)
        }
    }

    LiveSplitScreen(
        state = liveState,
        onHold = model::hold,
        onRelease = model::release,
        onEnd = model::end,
    )
}
