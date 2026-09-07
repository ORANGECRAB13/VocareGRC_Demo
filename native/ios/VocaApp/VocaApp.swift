import SwiftUI
import VocaKit
import VocaSession

/// The whole app target: build the environment, hand `RootView` a live screen
/// that can see `VocaSession`, and get out of the way. Everything else is in
/// the package.
@main
struct VocaApp: App {
    @StateObject private var env: AppEnvironment
    private let engine = AppleOfflineEngine()

    init() {
        let engine = self.engine
        let environment: AppEnvironment
        do {
            environment = try AppEnvironment.live(offline: engine)
        } catch {
            // A SwiftData container that will not open is unrecoverable for
            // history, but the app must still start; fall back to in-memory.
            environment = AppEnvironment.preview()
        }
        _env = StateObject(wrappedValue: environment)
    }

    var body: some Scene {
        WindowGroup {
            RootView(env: env) { launch, callbacks in
                LiveScreen(env: env, launch: launch, callbacks: callbacks, engine: engine)
            }
        }
    }
}

/// CONTRACT §6: VocaKit's screens only need the pair-status slice of the engine.
/// The app target is where the two protocols meet, so VocaKit never imports
/// VocaSession.
extension AppleOfflineEngine: @retroactive OfflineAvailability {}
