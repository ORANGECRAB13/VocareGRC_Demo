import Foundation
import StoreKit

// MARK: - Store abstraction (so PurchasesService is testable with a fake)

public struct StoreProduct: Equatable, Sendable {
    public var id: String
    public var displayPrice: String
    public var displayName: String
    public var description: String

    public init(id: String, displayPrice: String, displayName: String, description: String) {
        self.id = id
        self.displayPrice = displayPrice
        self.displayName = displayName
        self.description = description
    }
}

/// What the store says this Apple Account owns. `receipt` is the StoreKit 2
/// `jwsRepresentation` — the server verifies it, we never decide the tier.
public struct StoreEntitlement: Equatable, Sendable {
    public var productId: String
    public var receipt: String

    public init(productId: String, receipt: String) {
        self.productId = productId
        self.receipt = receipt
    }
}

public enum StorePurchaseOutcome: Equatable, Sendable {
    case purchased(receipt: String)
    case cancelled
    /// Ask-to-Buy / SCA — the transaction arrives later on `updates`.
    case pending
}

public enum StoreError: Error, Equatable, Sendable {
    case productNotFound
    case unverifiedTransaction
    case purchaseFailed(String)
    case unavailable
}

public struct StoreUpdate: Equatable, Sendable {
    public var productId: String
    public var revoked: Bool
    public init(productId: String, revoked: Bool) {
        self.productId = productId
        self.revoked = revoked
    }
}

public protocol StoreClient: Sendable {
    func product(id: String) async throws -> StoreProduct
    func purchase(id: String) async throws -> StorePurchaseOutcome
    /// Active, verified, unrevoked, unexpired entitlement for `id`, or nil.
    func currentEntitlement(id: String) async -> StoreEntitlement?
    /// Forces a refresh (`AppStore.sync`, which may prompt for a password) and
    /// re-reads entitlements. A cancelled prompt just falls through to a re-read.
    func restore(id: String) async -> StoreEntitlement?
    /// Renewals, lapses, refunds delivered while the app runs.
    func updates() -> AsyncStream<StoreUpdate>
}

// MARK: - StoreKit 2 (ported from ios/App/App/PurchasesPlugin.swift)

public struct StoreKitClient: StoreClient {
    public init() {}

    public func product(id: String) async throws -> StoreProduct {
        let products = try await Product.products(for: [id])
        guard let product = products.first else { throw StoreError.productNotFound }
        return StoreProduct(id: product.id, displayPrice: product.displayPrice,
                            displayName: product.displayName, description: product.description)
    }

    public func purchase(id: String) async throws -> StorePurchaseOutcome {
        let products = try await Product.products(for: [id])
        guard let product = products.first else { throw StoreError.productNotFound }
        let result: Product.PurchaseResult
        do {
            result = try await product.purchase()
        } catch {
            throw StoreError.purchaseFailed(error.localizedDescription)
        }
        switch result {
        case .success(let verification):
            guard case .verified(let transaction) = verification else {
                // StoreKit could not verify its own signature; the server would reject it anyway.
                throw StoreError.unverifiedTransaction
            }
            // Finish only after we hold the JWS. An unfinished transaction is
            // re-delivered on next launch, which is what we want if the app dies mid-purchase.
            let jws = verification.jwsRepresentation
            await transaction.finish()
            return .purchased(receipt: jws)
        case .userCancelled:
            return .cancelled
        case .pending:
            return .pending
        @unknown default:
            return .cancelled
        }
    }

    public func currentEntitlement(id: String) async -> StoreEntitlement? {
        for await result in Transaction.currentEntitlements {
            guard case .verified(let transaction) = result else { continue }
            guard transaction.productID == id else { continue }
            if transaction.revocationDate != nil { continue }
            if let expires = transaction.expirationDate, expires <= Date() { continue }
            return StoreEntitlement(productId: transaction.productID, receipt: result.jwsRepresentation)
        }
        return nil
    }

    public func restore(id: String) async -> StoreEntitlement? {
        try? await AppStore.sync()
        return await currentEntitlement(id: id)
    }

    public func updates() -> AsyncStream<StoreUpdate> {
        AsyncStream { continuation in
            let task = Task.detached {
                for await update in Transaction.updates {
                    guard case .verified(let transaction) = update else { continue }
                    await transaction.finish()
                    continuation.yield(StoreUpdate(productId: transaction.productID,
                                                   revoked: transaction.revocationDate != nil))
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
