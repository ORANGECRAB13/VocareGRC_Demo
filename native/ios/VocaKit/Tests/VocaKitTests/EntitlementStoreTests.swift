import XCTest
@testable import VocaKit

/// CONTRACT §2 (balance payload semantics) and §5 (tier routing).
/// Every balance comes from `native/fixtures/*.json` when the fixtures are
/// checked out, so a payload change in `bot.py` breaks these first.
@MainActor
final class EntitlementStoreTests: XCTestCase {

    // MARK: Helpers

    private func balance(_ fixture: String) throws -> Balance {
        let json = Fixtures.balanceJSON(fixture)
        return try JSONDecoder().decode(Balance.self, from: Data(json.utf8))
    }

    private func store(_ fixture: String?,
                       store: FakeStore = FakeStore(),
                       api: FakeEntitlementAPI? = nil) throws -> (EntitlementStore, FakeEntitlementAPI, Preferences) {
        let initial = try fixture.map { try balance($0) }
        let backing = api ?? FakeEntitlementAPI(balance: initial ?? Balance(tier: .free))
        let prefs = makePreferences()
        let subject = EntitlementStore(api: backing,
                                       purchases: PurchasesService(store: store),
                                       preferences: prefs,
                                       clientId: "client-test",
                                       initialBalance: initial)
        return (subject, backing, prefs)
    }

    // MARK: enforced / tier / budget

    func testFreeUnenforcedIsCloudAllowedWithInfiniteBudget() throws {
        // Current production: metering is recorded but nobody is refused.
        let (subject, _, _) = try store("balance_free_unenforced")
        XCTAssertEqual(subject.tier, .free)
        XCTAssertFalse(subject.isPro)
        XCTAssertFalse(subject.metered)
        XCTAssertTrue(subject.cloudAllowed)
        XCTAssertEqual(subject.budgetSeconds, .infinity)
    }

    func testFreeEnforcedIsNotCloudAllowedAndHasZeroBudget() throws {
        let (subject, _, _) = try store("balance_free_enforced")
        XCTAssertTrue(subject.metered)
        XCTAssertFalse(subject.cloudAllowed)
        XCTAssertEqual(subject.budgetSeconds, 0)
        XCTAssertEqual(subject.secondsLeft, 0)
    }

    func testProBudgetIsSecondsRemaining() throws {
        let (subject, _, _) = try store("balance_pro")
        XCTAssertTrue(subject.isPro)
        XCTAssertTrue(subject.cloudAllowed)
        XCTAssertEqual(subject.secondsLeft, 2700)
        XCTAssertEqual(subject.budgetSeconds, 2700)
    }

    func testMissingBalanceCountsAsEnforced() throws {
        // "A missing field means enforced" — and no balance at all is the
        // most conservative case of that.
        let (subject, _, _) = try store(nil)
        XCTAssertTrue(subject.metered)
        XCTAssertFalse(subject.cloudAllowed)
        XCTAssertEqual(subject.budgetSeconds, 0)
        XCTAssertFalse(subject.showsAds, "no balance yet → no ad slot")
    }

    func testSandboxBalanceIsNotDurableButStillSpendable() throws {
        let sandbox = try balance("balance_sandbox")
        XCTAssertTrue(sandbox.sandboxOk)
        XCTAssertFalse(sandbox.durable)
        let (subject, _, _) = try store("balance_sandbox")
        XCTAssertTrue(subject.isPro)
        XCTAssertTrue(subject.cloudAllowed)
        XCTAssertEqual(subject.budgetSeconds, TimeInterval(sandbox.secondsRemaining),
                       "an in-process sandbox balance is still a real budget")
    }

    // MARK: routing (§5)

    func testOfflineSwitchOnAndCapablePairGoesOffline() throws {
        let (subject, _, prefs) = try store("balance_pro")
        prefs.offlineMode = true
        XCTAssertEqual(subject.route(langA: "en", langB: "zh"), .offline)
    }

    func testNotCloudAllowedFallsBackToOfflineWhenPairIsCapable() throws {
        let (subject, _, prefs) = try store("balance_free_enforced")
        prefs.offlineMode = false          // user turned the switch off explicitly
        XCTAssertEqual(subject.route(langA: "en", langB: "zh"), .offline)
    }

    func testNotCloudAllowedAndUnsupportedPairGoesToUnsupportedPaywall() throws {
        let (subject, _, prefs) = try store("balance_free_enforced")
        prefs.offlineMode = false
        // yue is never offline (§4).
        XCTAssertEqual(subject.route(langA: "en", langB: "yue"), .paywall(.unsupported))
    }

    func testMeteredWithNoSecondsGoesToExhaustedPaywall() throws {
        let (subject, _, prefs) = try store("balance_pro_exhausted")
        prefs.offlineMode = false
        XCTAssertEqual(subject.route(langA: "en", langB: "yue"), .paywall(.exhausted))
        XCTAssertEqual(subject.upgradeReason, .exhausted)
    }

    func testCloudRouteWhenAllowedAndPairIsNotOffline() throws {
        let (subject, _, prefs) = try store("balance_pro")
        prefs.offlineMode = false
        XCTAssertEqual(subject.route(langA: "en", langB: "yue"), .cloud)
    }

    func testFreeUnenforcedWithOfflineSwitchOffStillReachesCloud() throws {
        let (subject, _, prefs) = try store("balance_free_unenforced")
        prefs.offlineMode = false
        XCTAssertEqual(subject.route(langA: "en", langB: "zh"), .cloud)
        XCTAssertEqual(subject.upgradeReason, .upsell)
    }

    // MARK: offline switch tri-state

    func testOfflineSwitchDefaultsToTierAndSticksOnceTouched() throws {
        let (free, _, freePrefs) = try store("balance_free_unenforced")
        XCTAssertNil(freePrefs.offlineMode)
        XCTAssertTrue(free.offlineMode, "free defaults to on-device")

        let (pro, _, proPrefs) = try store("balance_pro")
        XCTAssertNil(proPrefs.offlineMode)
        XCTAssertFalse(pro.offlineMode, "pro defaults to cloud")

        pro.setOfflineMode(true)
        XCTAssertEqual(proPrefs.offlineMode, true)
        XCTAssertTrue(pro.offlineMode, "a touched switch wins over the tier default")

        free.setOfflineMode(false)
        XCTAssertEqual(freePrefs.offlineMode, false)
        XCTAssertFalse(free.offlineMode)
    }

    // MARK: ads

    func testAdsOnlyForMeteredFreeUsers() throws {
        let (freeEnforced, _, _) = try store("balance_free_enforced")
        XCTAssertTrue(freeEnforced.showsAds)

        let (freeUnenforced, _, _) = try store("balance_free_unenforced")
        XCTAssertFalse(freeUnenforced.showsAds, "unmetered free users are not shown ads")

        let (pro, _, _) = try store("balance_pro")
        XCTAssertFalse(pro.showsAds, "never show ads to pro")
    }

    // MARK: store_reachable semantics (§2)

    func testSyncSendsHeldReceiptWithStoreReachableTrue() async throws {
        let fake = FakeStore()
        fake.held = StoreEntitlement(productId: "vocare_pro_monthly", receipt: "jws-abc")
        let api = FakeEntitlementAPI(balance: try balance("balance_pro"))
        let (subject, _, _) = try store(nil, store: fake, api: api)

        let fresh = await subject.sync()
        XCTAssertEqual(fresh?.tier, .pro)
        XCTAssertEqual(api.activateRequests.count, 1)
        XCTAssertEqual(api.activateRequests.first?.receipt, "jws-abc")
        XCTAssertEqual(api.activateRequests.first?.storeReachable, true)
        XCTAssertEqual(api.activateRequests.first?.platform, .ios)
        XCTAssertEqual(api.activateRequests.first?.subject, "client-test")
        XCTAssertEqual(api.entitlementCalls, 0)
    }

    func testSyncWithNoEntitlementIsAVerifiedNoSubscription() async throws {
        // reachable:true + empty receipt is the downgrade signal, not "couldn't ask".
        let fake = FakeStore()
        fake.held = nil
        let api = FakeEntitlementAPI(balance: try balance("balance_free_unenforced"))
        let (subject, _, _) = try store("balance_pro", store: fake, api: api)

        let fresh = await subject.sync()
        XCTAssertEqual(fresh?.tier, .free)
        XCTAssertEqual(api.activateRequests.first?.receipt, "")
        XCTAssertEqual(api.activateRequests.first?.storeReachable, true)
        XCTAssertEqual(subject.tier, .free, "a verified no-subscription downgrades")
    }

    func testAStoreWeCouldNotAskMustNotDowngrade() async throws {
        // §2: store_reachable:false = "couldn't ask". The client must not send
        // an empty receipt to /activate (that is the downgrade signal); it
        // falls back to GET /api/entitlement. Android: PlayPurchasesServiceTest
        // "a store we could not ask must not downgrade".
        let fake = FakeStore()
        fake.held = nil
        fake.entitlementError = .unavailable
        let api = FakeEntitlementAPI(balance: try balance("balance_pro"))
        let (subject, _, _) = try store("balance_pro", store: fake, api: api)

        let fresh = await subject.sync()
        XCTAssertTrue(api.activateRequests.isEmpty, "an unreachable store must never call /activate with an empty receipt")
        XCTAssertEqual(api.entitlementCalls, 1, "it reads the server's own record instead")
        XCTAssertEqual(fresh?.tier, .pro)
        XCTAssertTrue(subject.isPro, "a store we could not ask must not downgrade a paying user")
    }

    func testSyncFailureKeepsTheBalanceWeHad() async throws {
        let api = FakeEntitlementAPI(balance: try balance("balance_free_unenforced"))
        api.error = .network(code: -1009, description: "offline")
        let (subject, _, _) = try store("balance_pro", api: api)

        let fresh = await subject.sync()
        XCTAssertNil(fresh)
        XCTAssertTrue(subject.isPro, "an offline launch must not downgrade")
        XCTAssertFalse(subject.syncing)
    }

    func testApplyOnlyOverwritesWithANonNilBalance() throws {
        let (subject, _, _) = try store("balance_pro")
        subject.apply(nil)
        XCTAssertEqual(subject.secondsLeft, 2700)
        subject.apply(try balance("balance_pro_exhausted"))
        XCTAssertEqual(subject.secondsLeft, 0)
    }

    // MARK: purchase flow verdicts

    func testPurchaseNeedsServerVerificationBeforeItCountsAsPro() async throws {
        let fake = FakeStore()
        fake.purchaseOutcome = .purchased(receipt: "jws-new")
        let api = FakeEntitlementAPI(balance: try balance("balance_free_unenforced"))
        api.activateResponse = try balance("entitlement_activate_unverified")
        let (subject, _, _) = try store(nil, store: fake, api: api)

        let result = await subject.purchase()
        XCTAssertEqual(result, .unverified)
        XCTAssertFalse(subject.isPro)
    }

    func testVerifiedPurchaseActivatesPro() async throws {
        let fake = FakeStore()
        fake.purchaseOutcome = .purchased(receipt: "jws-new")
        let api = FakeEntitlementAPI(balance: try balance("balance_free_unenforced"))
        api.activateResponse = try balance("entitlement_activate_verified")
        let (subject, _, _) = try store(nil, store: fake, api: api)

        let result = await subject.purchase()
        guard case .activated(let fresh) = result else { return XCTFail("expected .activated, got \(result)") }
        XCTAssertEqual(fresh.tier, .pro)
        XCTAssertTrue(subject.isPro)
        XCTAssertEqual(api.activateRequests.last?.receipt, "jws-new")
    }

    func testCancelledAndPendingPurchasesDoNotTouchTheServer() async throws {
        for (outcome, expected) in [(StorePurchaseOutcome.cancelled, EntitlementStore.PurchaseFlowResult.cancelled),
                                    (.pending, .pending)] {
            let fake = FakeStore()
            fake.purchaseOutcome = outcome
            let api = FakeEntitlementAPI(balance: try balance("balance_free_unenforced"))
            let (subject, _, _) = try store(nil, store: fake, api: api)
            let result = await subject.purchase()
            XCTAssertEqual(result, expected)
            XCTAssertTrue(api.activateRequests.isEmpty)
        }
    }

    func testRestoreWithNothingOnTheAccountReportsNotFound() async throws {
        let (subject, _, _) = try store("balance_free_unenforced")
        let result = await subject.restore()
        XCTAssertEqual(result, .notFound)
    }
}
