import SwiftUI

/// The JSX `App` render switch. The live screen is supplied by the app target
/// because it needs `VocaSession`, which VocaKit cannot import.
public struct RootView<Live: View>: View {
    @ObservedObject private var env: AppEnvironment
    @ObservedObject private var router: AppRouter
    private let liveScreen: (SessionLaunch, LiveCallbacks) -> Live

    public init(env: AppEnvironment, @ViewBuilder liveScreen: @escaping (SessionLaunch, LiveCallbacks) -> Live) {
        self.env = env
        self.router = env.router
        self.liveScreen = liveScreen
    }

    public var body: some View {
        VStack(spacing: 0) {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if router.screen.showsTabBar {
                if env.entitlements.showsAds {
                    BannerAdView(ads: env.ads)
                }
                TabBar(active: router.tab) { router.go($0) }
            }
        }
        .background(VocaTheme.ground.ignoresSafeArea())
        .sheet(isPresented: Binding(get: { router.pendingLaunch != nil },
                                    set: { if !$0 { router.cancelMicDisclosure() } })) {
            MicDisclosureSheet(privacyURL: env.privacyURL,
                               onAccept: { router.acceptMicDisclosure() },
                               onCancel: { router.cancelMicDisclosure() })
        }
        .onAppear { env.start() }
        .animation(.easeInOut(duration: 0.2), value: router.screen)
    }

    @ViewBuilder
    private var content: some View {
        switch router.screen {
        case .home:
            HomeScreen(entitlements: env.entitlements, preferences: env.preferences, offline: env.offline,
                       onStart: { router.startFromHome() },
                       onPick: { router.pickLanguage($0) },
                       onUpgrade: { router.showPaywall() })
        case .languagePick(let side):
            LanguagePickerScreen(targetName: side == .a ? "Person A" : "Person B", side: side,
                                 selected: side == .a ? env.preferences.langA : env.preferences.langB,
                                 onBack: { router.goHome() },
                                 onSelect: { router.selectLanguage($0, for: side) })
        case .history:
            HistoryScreen(repository: env.historyRepository) { router.openDetail($0) }
        case .detail(let id):
            SessionDetailScreen(sessionId: id, repository: env.historyRepository) { router.screen = .history }
        case .settings:
            SettingsScreen(preferences: env.preferences, entitlements: env.entitlements, offline: env.offline,
                           privacyURL: env.privacyURL,
                           onUpgrade: { router.showPaywall() },
                           onStartOffline: { router.startOffline() })
        case .paywall(let reason):
            PaywallScreen(entitlements: env.entitlements, reason: reason,
                          langA: env.preferences.langA, langB: env.preferences.langB,
                          onClose: { router.closePaywall() },
                          onPurchased: { router.purchased($0) })
        case .live(let launch):
            liveScreen(launch, LiveCallbacks(
                onStop: { router.sessionStopped() },
                onExhausted: { router.sessionExhausted() },
                onError: { router.sessionFailed($0) },
                onBalance: { env.entitlements.apply($0) }
            ))
        case .error(let kind):
            ErrorScreen(kind: kind, onRetry: { router.retryAfterError() }, onBack: { router.goHome() })
        }
    }
}

/// What a live screen reports back to the shell.
public struct LiveCallbacks {
    public var onStop: () -> Void
    public var onExhausted: () -> Void
    public var onError: (ErrorKind) -> Void
    /// A balance returned by a consume call mid-session.
    public var onBalance: (Balance?) -> Void

    public init(onStop: @escaping () -> Void, onExhausted: @escaping () -> Void,
                onError: @escaping (ErrorKind) -> Void, onBalance: @escaping (Balance?) -> Void) {
        self.onStop = onStop
        self.onExhausted = onExhausted
        self.onError = onError
        self.onBalance = onBalance
    }
}
