import Foundation
import Combine

/// What a live screen is started with. The app target turns this into a
/// `VocaSession.SessionConfig` plus the right view model.
public struct SessionLaunch: Equatable, Sendable {
    public enum Mode: Equatable, Sendable {
        case cloud
        case offline
    }

    public var mode: Mode
    public var langA: String
    public var langB: String
    public var nameA: String
    public var nameB: String

    public init(mode: Mode, langA: String, langB: String, nameA: String = "Person A", nameB: String = "Person B") {
        self.mode = mode
        self.langA = langA
        self.langB = langB
        self.nameA = nameA
        self.nameB = nameB
    }
}

public enum ErrorKind: String, Equatable, Sendable {
    case mic
    case network
}

public enum Tab: String, CaseIterable, Equatable, Sendable {
    case home, translate, history, settings
}

public enum Screen: Equatable, Sendable {
    case home
    case languagePick(side: PickSide)
    case history
    case detail(sessionId: String)
    case settings
    case paywall(EntitlementStore.PaywallReason)
    case live(SessionLaunch)
    case error(ErrorKind)

    public enum PickSide: Equatable, Sendable { case a, b }

    public var isLive: Bool {
        if case .live = self { return true }
        return false
    }

    /// Tab bar only on the three top-level tabs (JSX `showTabBar`).
    public var showsTabBar: Bool {
        switch self {
        case .home, .history, .settings: return true
        default: return false
        }
    }
}

/// The JSX `App` state machine: one funnel (`start`) for every route into a
/// live session so tier routing sits beside the microphone disclosure.
@MainActor
public final class AppRouter: ObservableObject {
    @Published public var screen: Screen = .home
    @Published public var tab: Tab = .home
    /// Non-nil while the mic disclosure sheet is up.
    @Published public var pendingLaunch: (langA: String, langB: String, nameA: String, nameB: String)?

    private let entitlements: EntitlementStore
    private let preferences: Preferences
    /// The launch the paywall should resume after a successful purchase.
    private var interruptedLaunch: SessionLaunch?

    public init(entitlements: EntitlementStore, preferences: Preferences) {
        self.entitlements = entitlements
        self.preferences = preferences
    }

    public func go(_ tab: Tab) {
        self.tab = tab
        switch tab {
        case .home: screen = .home
        case .translate: startFromHome()
        case .history: screen = .history
        case .settings: screen = .settings
        }
    }

    public func goHome() {
        tab = .home
        screen = .home
    }

    /// Home's Start button and the Translate tab: straight in with the home pair.
    public func startFromHome() {
        start(langA: preferences.langA, langB: preferences.langB)
    }

    /// Every route into a live screen funnels through here.
    public func start(langA: String, langB: String, nameA: String = "Person A", nameB: String = "Person B") {
        tab = .translate
        if preferences.micConsent {
            enterSession(langA: langA, langB: langB, nameA: nameA, nameB: nameB)
        } else {
            pendingLaunch = (langA, langB, nameA, nameB)
        }
    }

    public func acceptMicDisclosure() {
        guard let pending = pendingLaunch else { return }
        pendingLaunch = nil
        preferences.micConsent = true
        enterSession(langA: pending.langA, langB: pending.langB, nameA: pending.nameA, nameB: pending.nameB)
    }

    public func cancelMicDisclosure() {
        pendingLaunch = nil
        if screen == .home { tab = .home }
    }

    /// Settings' "Start an offline session": the on-device engine end to end.
    public func startOffline() {
        screen = .live(SessionLaunch(mode: .offline, langA: preferences.langA, langB: preferences.langB))
    }

    private func enterSession(langA: String, langB: String, nameA: String, nameB: String) {
        preferences.langA = langA
        preferences.langB = langB
        switch entitlements.route(langA: langA, langB: langB) {
        case .offline:
            interruptedLaunch = nil
            screen = .live(SessionLaunch(mode: .offline, langA: langA, langB: langB, nameA: nameA, nameB: nameB))
        case .cloud:
            interruptedLaunch = nil
            screen = .live(SessionLaunch(mode: .cloud, langA: langA, langB: langB, nameA: nameA, nameB: nameB))
        case .paywall(let reason):
            interruptedLaunch = SessionLaunch(mode: .cloud, langA: langA, langB: langB, nameA: nameA, nameB: nameB)
            screen = .paywall(reason)
        }
    }

    public func showPaywall(_ reason: EntitlementStore.PaywallReason? = nil) {
        screen = .paywall(reason ?? entitlements.upgradeReason)
    }

    /// After a verified purchase: straight into the session they were trying to start.
    public func purchased(_ balance: Balance) {
        if let launch = interruptedLaunch, balance.secondsRemaining > 0 {
            interruptedLaunch = nil
            screen = .live(launch)
        } else {
            goHome()
        }
    }

    public func closePaywall() {
        interruptedLaunch = nil
        goHome()
    }

    // MARK: Live screen callbacks

    /// JSX `handleStop`: a finished session lands on History.
    public func sessionStopped() {
        tab = .history
        screen = .history
    }

    public func sessionExhausted() {
        screen = .paywall(.exhausted)
    }

    public func sessionFailed(_ kind: ErrorKind) {
        screen = .error(kind)
    }

    public func retryAfterError() {
        goHome()
    }

    public func openDetail(_ sessionId: String) {
        screen = .detail(sessionId: sessionId)
    }

    public func pickLanguage(_ side: Screen.PickSide) {
        screen = .languagePick(side: side)
    }

    public func selectLanguage(_ code: String, for side: Screen.PickSide) {
        switch side {
        case .a: preferences.langA = code
        case .b: preferences.langB = code
        }
        screen = .home
    }
}
