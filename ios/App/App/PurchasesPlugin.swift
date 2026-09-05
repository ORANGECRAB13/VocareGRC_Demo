import Capacitor
import Foundation
import StoreKit

/// Auto-renewing subscription purchases via StoreKit 2.
///
/// The plugin deliberately does NOT decide whether the user is entitled. It
/// hands the server a signed transaction (`jwsRepresentation`) and the server
/// verifies it against Apple's certificate chain. StoreKit's own
/// `VerificationResult` check runs here too, but only to avoid sending obvious
/// junk over the network — a jailbroken device can defeat client-side
/// verification, which is exactly why the balance lives server-side.
///
/// StoreKit 2 needs iOS 15. The app targets far higher than that, but the
/// availability gate is explicit so the failure is a clear message rather than
/// a link error.
@objc(PurchasesPlugin)
public final class PurchasesPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PurchasesPlugin"
    public let jsName = "VocarePurchases"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getProduct", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "purchase", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "restore", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "currentEntitlement", returnType: CAPPluginReturnPromise)
    ]

    /// Must match the product id configured in App Store Connect and the
    /// PRO_PRODUCT_ID constant in vocare-app.jsx.
    private static let productID = "vocare_pro_monthly"

    private var updatesTask: Task<Void, Never>?

    // MARK: - Lifecycle

    /// A subscription can renew, lapse, be refunded or be upgraded while the app
    /// is backgrounded or closed. Without this listener the app would keep
    /// showing a stale tier until the next cold start, and — worse — a refunded
    /// subscriber would keep spending minutes. The listener runs for the whole
    /// life of the plugin and pushes every change to the webview, which
    /// re-verifies it with the server.
    override public func load() {
        updatesTask = Task.detached { [weak self] in
            for await update in Transaction.updates {
                guard let self else { return }
                guard case .verified(let transaction) = update else { continue }
                await transaction.finish()
                self.notifyListeners("entitlementChanged", data: [
                    "productId": transaction.productID,
                    "revoked": transaction.revocationDate != nil
                ])
            }
        }
    }

    deinit {
        updatesTask?.cancel()
    }

    // MARK: - Methods

    @objc func isAvailable(_ call: CAPPluginCall) {
        if #available(iOS 15.0, *) {
            call.resolve(["available": true, "platform": "ios"])
        } else {
            call.resolve([
                "available": false,
                "platform": "ios",
                "reason": "Subscriptions need iOS 15 or later."
            ])
        }
    }

    /// The localized price string, so the paywall can show what the store will
    /// actually charge in the user's storefront rather than a hardcoded A$29.99.
    @objc func getProduct(_ call: CAPPluginCall) {
        Task {
            do {
                let products = try await Product.products(for: [Self.productID])
                guard let product = products.first else {
                    call.reject("product_not_found")
                    return
                }
                call.resolve([
                    "productId": product.id,
                    "displayPrice": product.displayPrice,
                    "displayName": product.displayName,
                    "description": product.description
                ])
            } catch {
                call.reject("product_lookup_failed", nil, error)
            }
        }
    }

    @objc func purchase(_ call: CAPPluginCall) {
        Task {
            do {
                let products = try await Product.products(for: [Self.productID])
                guard let product = products.first else {
                    call.reject("product_not_found")
                    return
                }

                let result = try await product.purchase()
                switch result {
                case .success(let verification):
                    guard case .verified(let transaction) = verification else {
                        // StoreKit could not verify its own signature. Do not
                        // send it on; the server would reject it anyway.
                        call.reject("unverified_transaction")
                        return
                    }
                    // Finish only after we hold the JWS. An unfinished
                    // transaction is re-delivered on next launch, which is the
                    // behaviour we want if the app dies mid-purchase.
                    let jws = verification.jwsRepresentation
                    await transaction.finish()
                    call.resolve(["status": "purchased", "jws": jws])

                case .userCancelled:
                    call.resolve(["status": "cancelled"])

                case .pending:
                    // Ask-to-Buy or SCA. The transaction arrives later on the
                    // Transaction.updates listener, so this is not an error.
                    call.resolve(["status": "pending"])

                @unknown default:
                    call.resolve(["status": "unknown"])
                }
            } catch {
                call.reject("purchase_failed", nil, error)
            }
        }
    }

    /// StoreKit 2 has no separate "restore" concept — current entitlements are
    /// always readable for the signed-in Apple Account. `AppStore.sync()` is only
    /// needed to force a refresh, and it prompts for a password, so it is called
    /// only from the explicit Restore button.
    @objc func restore(_ call: CAPPluginCall) {
        Task {
            do {
                try await AppStore.sync()
            } catch {
                // A cancelled password prompt lands here. Fall through and read
                // whatever entitlements we already have rather than failing.
                CAPLog.print("[Purchases] AppStore.sync failed: \(error)")
            }
            let entitlement = await Self.activeEntitlement()
            call.resolve(entitlement)
        }
    }

    @objc func currentEntitlement(_ call: CAPPluginCall) {
        Task {
            call.resolve(await Self.activeEntitlement())
        }
    }

    // MARK: - Helpers

    /// The signed transaction for an active subscription, or `active: false`.
    /// Revoked (refunded) transactions are treated as inactive.
    private static func activeEntitlement() async -> [String: Any] {
        for await result in Transaction.currentEntitlements {
            guard case .verified(let transaction) = result else { continue }
            guard transaction.productID == productID else { continue }
            if transaction.revocationDate != nil { continue }
            if let expires = transaction.expirationDate, expires <= Date() { continue }
            return ["active": true, "jws": result.jwsRepresentation, "productId": transaction.productID]
        }
        return ["active": false]
    }
}
