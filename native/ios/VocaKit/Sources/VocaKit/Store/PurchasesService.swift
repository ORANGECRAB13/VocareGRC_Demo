import Foundation

/// The JSX purchase helpers (`purchasePro`, `restorePurchases`, `currentReceipt`,
/// `storePrice`) over a `StoreClient`. The service never decides the tier —
/// it hands receipts to `EntitlementStore`, which asks the server.
public struct PurchasesService: Sendable {
    /// Must match App Store Connect and `PRO_PRODUCT_ID` in the JSX.
    public static let proProductID = "vocare_pro_monthly"
    public static let planPriceFallback = "A$29.99"
    public static let planMinutes = 60

    public enum Status: Equatable, Sendable {
        case purchased
        case cancelled
        case pending
        /// Restore found nothing on this Apple Account.
        case none
    }

    public struct Result: Equatable, Sendable {
        public var status: Status
        public var receipt: String
        public init(status: Status, receipt: String = "") {
            self.status = status
            self.receipt = receipt
        }
    }

    /// `reachable:false` means the store could not be asked; the server must not
    /// downgrade on that. `reachable:true` with an empty receipt is a verified
    /// "no subscription".
    public struct Held: Equatable, Sendable {
        public var reachable: Bool
        public var receipt: String
        public init(reachable: Bool, receipt: String) {
            self.reachable = reachable
            self.receipt = receipt
        }
    }

    private let store: StoreClient
    private let productID: String

    public init(store: StoreClient = StoreKitClient(), productID: String = PurchasesService.proProductID) {
        self.store = store
        self.productID = productID
    }

    /// The storefront's own price so the paywall shows what will actually be charged.
    public func displayPrice() async -> String? {
        (try? await store.product(id: productID))?.displayPrice
    }

    public func currentReceipt() async -> Held {
        guard let held = await store.currentEntitlement(id: productID) else {
            return Held(reachable: true, receipt: "")
        }
        return Held(reachable: true, receipt: held.receipt)
    }

    public func purchase() async throws -> Result {
        switch try await store.purchase(id: productID) {
        case .purchased(let receipt):
            guard !receipt.isEmpty else { throw StoreError.purchaseFailed("purchase_receipt_missing") }
            return Result(status: .purchased, receipt: receipt)
        case .cancelled:
            return Result(status: .cancelled)
        case .pending:
            return Result(status: .pending)
        }
    }

    public func restore() async -> Result {
        guard let held = await store.restore(id: productID) else { return Result(status: .none) }
        return Result(status: .purchased, receipt: held.receipt)
    }

    public func updates() -> AsyncStream<StoreUpdate> { store.updates() }
}
