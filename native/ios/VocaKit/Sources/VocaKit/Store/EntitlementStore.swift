import Foundation
import Combine

/// Tier, balance and the routing decisions built on them (JSX `App` state:
/// `tier`, `metered`, `cloudAllowed`, `offlineMode`, `enterSession`).
///
/// The store is the source of truth for *whether* someone is subscribed; the
/// server is the source of truth for *how many minutes they have left*.
@MainActor
public final class EntitlementStore: ObservableObject {
    public enum PaywallReason: String, Equatable, Sendable {
        /// A free user who wants the cloud engine.
        case upsell
        /// A free user whose pair has no on-device model.
        case unsupported
        /// A subscriber who spent this month's minutes.
        case exhausted
    }

    public enum StartRoute: Equatable, Sendable {
        case offline
        case cloud
        case paywall(PaywallReason)
    }

    public enum PurchaseFlowResult: Equatable, Sendable {
        case activated(Balance)
        case cancelled
        case pending
        case notFound
        case unverified
        case failed(String)
    }

    @Published public private(set) var balance: Balance?
    @Published public private(set) var displayPrice: String = PurchasesService.planPriceFallback
    @Published public private(set) var syncing = false

    public let preferences: Preferences
    public let clientId: String
    private let api: VocaEntitlementAPI
    private let purchases: PurchasesService
    private var updatesTask: Task<Void, Never>?

    public init(api: VocaEntitlementAPI, purchases: PurchasesService, preferences: Preferences, clientId: String,
                initialBalance: Balance? = nil) {
        self.api = api
        self.purchases = purchases
        self.preferences = preferences
        self.clientId = clientId
        self.balance = initialBalance
    }

    deinit { updatesTask?.cancel() }

    // MARK: Derived state

    public var tier: Tier { balance?.tier == .pro ? .pro : .free }
    public var isPro: Bool { tier == .pro }
    public var secondsLeft: Int { balance?.secondsRemaining ?? 0 }
    /// `enforced:false` (current production) → metering is recorded but nobody is refused.
    public var metered: Bool { balance?.enforced ?? true }
    public var cloudAllowed: Bool { isPro || !metered }
    /// `.infinity` when unmetered — what `SessionConfig.budgetSeconds` wants.
    public var budgetSeconds: TimeInterval { metered ? TimeInterval(secondsLeft) : .infinity }
    /// Free users default to on-device; a touched switch wins for good.
    public var offlineMode: Bool { preferences.offlineMode ?? !isPro }
    /// Ads: free tier, metered, and never on a live screen (the router checks that).
    public var showsAds: Bool { balance != nil && tier == .free && metered }

    public func setOfflineMode(_ on: Bool) { preferences.offlineMode = on }

    /// JSX `enterSession` routing.
    public func route(langA: String, langB: String) -> StartRoute {
        let pairOffline = Languages.canTranslateOffline(langA, langB)
        if offlineMode && pairOffline { return .offline }
        if !cloudAllowed { return pairOffline ? .offline : .paywall(.unsupported) }
        if metered && secondsLeft <= 0 { return .paywall(.exhausted) }
        return .cloud
    }

    /// The tier chip / Settings upgrade row: exhausted pro goes to the allowance copy.
    public var upgradeReason: PaywallReason { isPro && secondsLeft <= 0 ? .exhausted : .upsell }

    // MARK: Sync

    /// Ask the store what the user owns, tell the server, read the balance back
    /// (JSX `syncStoreEntitlement`). Errors are swallowed: an offline launch
    /// keeps whatever balance we had.
    @discardableResult
    public func sync() async -> Balance? {
        syncing = true
        defer { syncing = false }
        let held = await purchases.currentReceipt()
        do {
            let fresh: Balance
            if !held.reachable && held.receipt.isEmpty {
                fresh = try await api.entitlement(subject: clientId)
            } else {
                fresh = try await api.activate(ActivateRequest(subject: clientId, platform: .ios,
                                                               receipt: held.receipt, storeReachable: held.reachable))
            }
            balance = fresh
            return fresh
        } catch {
            return nil
        }
    }

    /// Sync on launch, listen for store updates, and refresh the localized price.
    public func start() {
        Task { await sync() }
        Task { if let price = await purchases.displayPrice() { displayPrice = price } }
        updatesTask?.cancel()
        updatesTask = Task { [weak self] in
            guard let self else { return }
            for await _ in purchases.updates() {
                await self.sync()
            }
        }
    }

    /// Applies a balance returned by a consume call mid-session.
    public func apply(_ fresh: Balance?) {
        if let fresh { balance = fresh }
    }

    // MARK: Purchase flow (JSX PaywallScreen.run)

    public func purchase() async -> PurchaseFlowResult {
        do {
            return await verify(try await purchases.purchase())
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    public func restore() async -> PurchaseFlowResult {
        await verify(await purchases.restore())
    }

    private func verify(_ result: PurchasesService.Result) async -> PurchaseFlowResult {
        switch result.status {
        case .cancelled: return .cancelled
        case .pending: return .pending
        case .none: return .notFound
        case .purchased: break
        }
        do {
            // The server decides the tier — it re-checks the receipt with Apple
            // before granting a single minute.
            let fresh = try await api.activate(ActivateRequest(subject: clientId, platform: .ios,
                                                               receipt: result.receipt, storeReachable: true))
            guard fresh.verified == true, fresh.tier == .pro else { return .unverified }
            balance = fresh
            return .activated(fresh)
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
