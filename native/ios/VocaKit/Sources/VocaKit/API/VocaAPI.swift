import Foundation

// MARK: - CONTRACT §6 — implemented in VocaKit/API, injected into VocaSession.

public protocol VocaAPI: Sendable {
    func iceServers() async throws -> [ICEServer]
    /// Returns the `session_id`.
    func createSession(_ req: CreateSessionRequest) async throws -> String
    /// Throws `APIError.paymentRequired(balance)` on 402.
    func offer(_ req: OfferRequest) async throws -> OfferAnswer
    func ptt(pcId: String, action: PTTAction) async throws -> PTTResult
    func poll(sessionId: String) async throws -> PollResponse
    /// Fire-and-forget; never throws.
    func hangup(pcId: String) async
    /// Reports the session's TOTAL elapsed seconds (idempotent watermark).
    /// Returns nil on any failure — usage reporting never breaks a session.
    func consume(subject: String, sessionId: String, seconds: Int) async -> Balance?
}

/// The endpoints only the app shell (VocaKit) needs: entitlement and history.
/// Kept separate so `VocaSession` is handed exactly the surface in §6.
public protocol VocaEntitlementAPI: Sendable {
    func entitlement(subject: String) async throws -> Balance
    func activate(_ req: ActivateRequest) async throws -> Balance
}

public protocol VocaHistoryAPI: Sendable {
    func sessions(clientId: String) async throws -> [SessionSummary]
    func session(id: String) async throws -> SessionDetail?
}

public typealias VocaFullAPI = VocaAPI & VocaEntitlementAPI & VocaHistoryAPI
