import SwiftUI

@main
struct VocareWatchApp: App {
    @StateObject private var model = WatchTranslationModel()

    var body: some Scene {
        WindowGroup {
            WatchContentView()
                .environmentObject(model)
        }
    }
}
