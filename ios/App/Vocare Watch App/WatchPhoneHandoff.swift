import Foundation
import WatchConnectivity

final class WatchPhoneHandoff: NSObject, WCSessionDelegate {
    static let shared = WatchPhoneHandoff()

    private let queue = DispatchQueue(label: "vocare.watch.handoff")
    private var pending: [[String: Any]] = []
    private var isActivated = false

    private override init() {
        super.init()
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
    }

    func transfer(session: WatchConversationSession, status: String) {
        guard WCSession.isSupported() else { return }
        let payload = session.handoffPayload(status: status)
        queue.async { [weak self] in
            guard let self else { return }
            if self.isActivated {
                WCSession.default.transferUserInfo(payload)
            } else {
                self.pending.append(payload)
            }
        }
    }

    func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        guard activationState == .activated else { return }
        queue.async { [weak self] in
            guard let self else { return }
            self.isActivated = true
            for payload in self.pending {
                WCSession.default.transferUserInfo(payload)
            }
            self.pending.removeAll()
        }
    }
}
