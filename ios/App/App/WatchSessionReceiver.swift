import Foundation
import WatchConnectivity

final class WatchSessionReceiver: NSObject, WCSessionDelegate {
    static let shared = WatchSessionReceiver()

    func start() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {}

    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { session.activate() }

    func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        save(userInfo)
    }

    private func save(_ payload: [String: Any]) {
        guard let sessionId = payload["sessionId"] as? String else { return }
        let input = TranslationSessionInput(
            sessionId: sessionId,
            callerName: payload["callerName"] as? String,
            topic: payload["topic"] as? String,
            languageA: payload["languageA"] as? String,
            languageB: payload["languageB"] as? String,
            participantA: "Apple Watch",
            participantB: "Translation",
            status: payload["status"] as? String,
            durationSeconds: payload["durationSeconds"] as? Int,
            transcriptJSON: payload["transcriptJSON"] as? String,
            startedAt: (payload["startedAt"] as? String).flatMap { ISO8601DateFormatter().date(from: $0) }
        )
        Task { @MainActor in
            do { _ = try TranslationHistoryStore.shared.upsert(input) }
            catch { NSLog("Could not save Apple Watch translation history: %@", error.localizedDescription) }
        }
    }
}
