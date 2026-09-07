import XCTest
@testable import VocaKit

/// CONTRACT §8: purchases service with a fake store — purchased / cancelled /
/// pending / restore. The service never decides the tier; it only carries
/// receipts, so every assertion here is about what it hands upwards.
final class PurchasesServiceTests: XCTestCase {

    private func service(_ store: FakeStore) -> PurchasesService {
        PurchasesService(store: store)
    }

    func testPurchasedReturnsTheReceipt() async throws {
        let store = FakeStore()
        store.purchaseOutcome = .purchased(receipt: "jws-1")
        let result = try await service(store).purchase()
        XCTAssertEqual(result, PurchasesService.Result(status: .purchased, receipt: "jws-1"))
        XCTAssertEqual(store.purchaseCalls, 1)
    }

    func testCancelledCarriesNoReceipt() async throws {
        let store = FakeStore()
        store.purchaseOutcome = .cancelled
        let result = try await service(store).purchase()
        XCTAssertEqual(result.status, .cancelled)
        XCTAssertTrue(result.receipt.isEmpty)
    }

    func testPendingCarriesNoReceipt() async throws {
        let store = FakeStore()
        store.purchaseOutcome = .pending
        let result = try await service(store).purchase()
        XCTAssertEqual(result.status, .pending)
        XCTAssertTrue(result.receipt.isEmpty)
    }

    func testPurchasedWithAnEmptyReceiptIsAFailureNotASuccess() async {
        let store = FakeStore()
        store.purchaseOutcome = .purchased(receipt: "")
        do {
            _ = try await service(store).purchase()
            XCTFail("an empty receipt must not read as a successful purchase")
        } catch {
            XCTAssertEqual(error as? StoreError, .purchaseFailed("purchase_receipt_missing"))
        }
    }

    func testStoreErrorsPropagate() async {
        let store = FakeStore()
        store.purchaseError = .productNotFound
        do {
            _ = try await service(store).purchase()
            XCTFail("expected the store error to propagate")
        } catch {
            XCTAssertEqual(error as? StoreError, .productNotFound)
        }
    }

    func testRestoreFindsAnExistingSubscription() async {
        let store = FakeStore()
        store.restored = StoreEntitlement(productId: PurchasesService.proProductID, receipt: "jws-restored")
        let result = await service(store).restore()
        XCTAssertEqual(result, PurchasesService.Result(status: .purchased, receipt: "jws-restored"))
        XCTAssertEqual(store.restoreCalls, 1)
    }

    func testRestoreFindsNothing() async {
        let store = FakeStore()
        let result = await service(store).restore()
        XCTAssertEqual(result.status, .none)
        XCTAssertTrue(result.receipt.isEmpty)
    }

    // MARK: currentReceipt → the `store_reachable` payload (§2)

    func testCurrentReceiptWithASubscription() async {
        let store = FakeStore()
        store.held = StoreEntitlement(productId: PurchasesService.proProductID, receipt: "jws-held")
        let held = await service(store).currentReceipt()
        XCTAssertEqual(held, PurchasesService.Held(reachable: true, receipt: "jws-held"))
    }

    func testCurrentReceiptWithoutASubscriptionIsAVerifiedNo() async {
        let held = await service(FakeStore()).currentReceipt()
        XCTAssertTrue(held.reachable, "reachable:true + empty receipt is a verified 'no subscription'")
        XCTAssertTrue(held.receipt.isEmpty)
    }

    // MARK: price

    func testDisplayPriceComesFromTheStorefront() async {
        let store = FakeStore()
        store.product = StoreProduct(id: PurchasesService.proProductID, displayPrice: "¥4,800",
                                     displayName: "Voca Pro", description: "60 minutes")
        let price = await service(store).displayPrice()
        XCTAssertEqual(price, "¥4,800")
    }

    func testDisplayPriceIsNilWhenTheProductIsMissing() async {
        let store = FakeStore()
        store.product = nil
        let price = await service(store).displayPrice()
        XCTAssertNil(price, "the paywall falls back to \(PurchasesService.planPriceFallback)")
    }

    func testProductIdMatchesTheContract() {
        XCTAssertEqual(PurchasesService.proProductID, "vocare_pro_monthly")
        XCTAssertEqual(PurchasesService.planMinutes, 60)
        XCTAssertEqual(PurchasesService.planPriceFallback, "A$29.99")
    }
}
