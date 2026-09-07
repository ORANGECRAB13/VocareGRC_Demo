import SwiftUI
import VocaKit

// MARK: - CONTRACT §5 live screen host
//
// §6 places this file in `VocaKit/Screens`, but VocaKit cannot import
// VocaSession (the dependency runs the other way, and `LiveSplitView` plus both
// view models live here), so the host lives in `VocaSession/UI` instead. The app
// target composes it into `VocaKit.RootView`'s `liveScreen:` builder, which was
// already written generically for exactly this reason.
//
// Routing is decided before we get here (`AppRouter.enterSession` →
// `EntitlementStore.route`); `SessionLaunch.mode` only says which view model to
// build.

public struct LiveScreen: View {
    private let env: AppEnvironment
    private let launch: SessionLaunch
    private let callbacks: LiveCallbacks
    private let engine: OfflineEngine

    public init(env: AppEnvironment,
                launch: SessionLaunch,
                callbacks: LiveCallbacks,
                engine: OfflineEngine) {
        self.env = env
        self.launch = launch
        self.callbacks = callbacks
        self.engine = engine
    }

    public var body: some View {
        switch launch.mode {
        case .cloud:
            CloudLiveScreen(env: env, launch: launch, callbacks: callbacks)
                .id("cloud-\(launch.langA)-\(launch.langB)")
        case .offline:
            OfflineLiveScreen(launch: launch, callbacks: callbacks, engine: engine, clientId: env.clientId)
                .id("offline-\(launch.langA)-\(launch.langB)")
        }
    }

    /// The §6 `SessionConfig` for a launch. Budget is `.infinity` when metering
    /// is off (`enforced:false`), which is current production.
    static func config(for launch: SessionLaunch, clientId: String, budgetSeconds: TimeInterval) -> SessionConfig {
        SessionConfig(langA: launch.langA, langB: launch.langB,
                      nameA: launch.nameA, nameB: launch.nameB,
                      clientId: clientId, budgetSeconds: budgetSeconds)
    }
}

// MARK: - Cloud

private struct CloudLiveScreen: View {
    let env: AppEnvironment
    let launch: SessionLaunch
    let callbacks: LiveCallbacks

    @StateObject private var model: CloudSessionViewModel

    init(env: AppEnvironment, launch: SessionLaunch, callbacks: LiveCallbacks) {
        self.env = env
        self.launch = launch
        self.callbacks = callbacks
        let config = LiveScreen.config(for: launch, clientId: env.clientId,
                                       budgetSeconds: env.entitlements.budgetSeconds)
        _model = StateObject(wrappedValue: CloudSessionViewModel(config: config,
                                                                api: env.api,
                                                                history: env.history))
    }

    var body: some View {
        LiveSplitView(state: model.state,
                      onHold: { model.hold(side: $0) },
                      onRelease: { model.release(side: $0) },
                      onEnd: { model.end() })
            .onAppear { model.start() }
            .onChange(of: model.state.phase) { _, phase in
                report(phase, callbacks: callbacks, refreshBalance: true, env: env)
            }
    }
}

// MARK: - Offline

private struct OfflineLiveScreen: View {
    let launch: SessionLaunch
    let callbacks: LiveCallbacks

    @StateObject private var model: OfflineSessionViewModel

    init(launch: SessionLaunch, callbacks: LiveCallbacks, engine: OfflineEngine, clientId: String) {
        self.launch = launch
        self.callbacks = callbacks
        let config = LiveScreen.config(for: launch, clientId: clientId, budgetSeconds: .infinity)
        _model = StateObject(wrappedValue: OfflineSessionViewModel(config: config, engine: engine))
    }

    var body: some View {
        LiveSplitView(state: model.state,
                      onHold: { model.hold(side: $0) },
                      onRelease: { model.release(side: $0) },
                      onEnd: { model.end() },
                      onSwap: { model.swapLanguages() })
            .onChange(of: model.state.phase) { _, phase in
                report(phase, callbacks: callbacks, refreshBalance: false, env: nil)
            }
    }
}

// MARK: - Phase → shell routing

@MainActor
private func report(_ phase: LivePhase, callbacks: LiveCallbacks,
                    refreshBalance: Bool, env: AppEnvironment?) {
    switch phase {
    case .connecting, .live:
        return
    case .ended(let reason):
        if refreshBalance, let env {
            Task { @MainActor in callbacks.onBalance(await env.entitlements.sync()) }
        }
        switch reason {
        case .user, .serverClosed: callbacks.onStop()
        case .exhausted: callbacks.onExhausted()
        }
    case .error(let error):
        switch error {
        case .mic: callbacks.onError(.mic)
        case .network: callbacks.onError(.network)
        }
    }
}
