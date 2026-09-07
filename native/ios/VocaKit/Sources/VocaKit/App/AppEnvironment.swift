import Foundation
import SwiftUI

/// Everything the screens need, built once at launch by the app target.
@MainActor
public final class AppEnvironment: ObservableObject {
    public let api: VocaFullAPI
    public let clientId: String
    public let preferences: Preferences
    public let entitlements: EntitlementStore
    public let history: HistoryStore
    public let historyRepository: HistoryRepository
    public let ads: AdsService
    public let offline: OfflineAvailability
    public let router: AppRouter
    /// Where the legal links point (JSX `${apiBase()}/privacy`).
    public var privacyURL: URL { (api as? LiveAPIClient)?.baseURL.appendingPathComponent("privacy") ?? LiveAPIClient.productionBase.appendingPathComponent("privacy") }

    public init(api: VocaFullAPI, clientId: String, preferences: Preferences, entitlements: EntitlementStore,
                history: HistoryStore, ads: AdsService, offline: OfflineAvailability) {
        self.api = api
        self.clientId = clientId
        self.preferences = preferences
        self.entitlements = entitlements
        self.history = history
        self.historyRepository = HistoryRepository(store: history, api: api, clientId: clientId)
        self.ads = ads
        self.offline = offline
        self.router = AppRouter(entitlements: entitlements, preferences: preferences)
    }

    /// The production wiring. `offline` is supplied by the app target because
    /// the engine lives in VocaSession.
    public static func live(offline: OfflineAvailability, bundle: Bundle = .main) throws -> AppEnvironment {
        let api = LiveAPIClient.fromBundle(bundle)
        let clientId = ClientID.current()
        let preferences = Preferences()
        let entitlements = EntitlementStore(api: api, purchases: PurchasesService(), preferences: preferences, clientId: clientId)
        return AppEnvironment(api: api, clientId: clientId, preferences: preferences, entitlements: entitlements,
                              history: try HistoryStore.persistent(), ads: AdsService(bundle: bundle), offline: offline)
    }

    /// Deterministic wiring for previews and render tests: no network, no store, no ads.
    public static func preview(balance: Balance? = Balance(tier: .free, enforced: false),
                               offlinePairs: PairStatus = .installed) -> AppEnvironment {
        let defaults = UserDefaults(suiteName: "vocare.preview.\(UUID().uuidString)")!
        let preferences = Preferences(defaults: defaults)
        let api = PreviewAPI(balance: balance ?? Balance(tier: .free))
        let entitlements = EntitlementStore(api: api, purchases: PurchasesService(store: PreviewStore()),
                                            preferences: preferences, clientId: "preview", initialBalance: balance)
        let history = (try? HistoryStore.inMemory()) ?? (try! HistoryStore.inMemory())
        return AppEnvironment(api: api, clientId: "preview", preferences: preferences, entitlements: entitlements,
                              history: history, ads: AdsService(bannerUnitID: nil, applicationID: nil),
                              offline: ConstantOfflineAvailability(offlinePairs))
    }

    public func start() {
        VocaTheme.registerFontsOnce()
        entitlements.start()
    }
}

/// Every pair reports the same status. Previews, tests, and the "no engine" fallback.
public struct ConstantOfflineAvailability: OfflineAvailability {
    public let status: PairStatus
    public init(_ status: PairStatus) { self.status = status }
    public func pairStatus(_ a: String, _ b: String) async -> PairStatus {
        Languages.canTranslateOffline(a, b) ? status : .unsupported
    }
    public func prepare(_ a: String, _ b: String) async throws -> Bool { status != .unsupported }
}

/// A canned API for previews/tests: sessions fail fast, entitlement is fixed.
public struct PreviewAPI: VocaFullAPI {
    public var balance: Balance
    public var sessions: [SessionSummary] = []
    public var details: [String: SessionDetail] = [:]

    public init(balance: Balance, sessions: [SessionSummary] = [], details: [String: SessionDetail] = [:]) {
        self.balance = balance
        self.sessions = sessions
        self.details = details
    }

    public func iceServers() async throws -> [ICEServer] { throw APIError.noICEServers }
    public func createSession(_ req: CreateSessionRequest) async throws -> String { throw APIError.network(code: -1009, description: "preview") }
    public func offer(_ req: OfferRequest) async throws -> OfferAnswer { throw APIError.network(code: -1009, description: "preview") }
    public func ptt(pcId: String, action: PTTAction) async throws -> PTTResult { PTTResult() }
    public func poll(sessionId: String) async throws -> PollResponse { PollResponse() }
    public func hangup(pcId: String) async {}
    public func consume(subject: String, sessionId: String, seconds: Int) async -> Balance? { balance }
    public func entitlement(subject: String) async throws -> Balance { balance }
    public func activate(_ req: ActivateRequest) async throws -> Balance { balance }
    public func sessions(clientId: String) async throws -> [SessionSummary] { sessions }
    public func session(id: String) async throws -> SessionDetail? { details[id] }
}

/// A store with no product and nothing owned.
public struct PreviewStore: StoreClient {
    public init() {}
    public func product(id: String) async throws -> StoreProduct { throw StoreError.productNotFound }
    public func purchase(id: String) async throws -> StorePurchaseOutcome { .cancelled }
    public func currentEntitlement(id: String) async -> StoreEntitlement? { nil }
    public func restore(id: String) async -> StoreEntitlement? { nil }
    public func updates() -> AsyncStream<StoreUpdate> { AsyncStream { $0.finish() } }
}
