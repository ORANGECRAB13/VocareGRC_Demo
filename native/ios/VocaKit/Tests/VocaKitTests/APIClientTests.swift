import XCTest
@testable import VocaKit

final class APIClientTests: XCTestCase {
    var client: LiveAPIClient!

    override func setUp() {
        super.setUp()
        StubProtocol.reset()
        client = makeStubClient()
    }

    // MARK: /api/ice

    func testIceServersDecodesFixtureAndRequestsRightPath() async throws {
        if let data = try? Fixtures.data("ice") {
            StubProtocol.enqueue(data: data)
        } else {
            StubProtocol.enqueue(json: #"{"iceServers":[{"urls":"stun:stun.l.google.com:19302"},{"urls":["turn:turn.test:3478?transport=udp"],"username":"u","credential":"c"}]}"#)
        }
        let servers = try await client.iceServers()
        XCTAssertFalse(servers.isEmpty)
        XCTAssertTrue(servers.allSatisfy { !$0.urls.isEmpty })
        let req = try XCTUnwrap(StubProtocol.requests.last?.request)
        XCTAssertEqual(req.httpMethod, "GET")
        XCTAssertEqual(req.url?.path, "/api/ice")
        // At least one TURN entry carries credentials.
        XCTAssertTrue(servers.contains { $0.username != nil && $0.credential != nil })
    }

    func testIceServersEmptyListIsTypedError() async {
        StubProtocol.enqueue(json: #"{"iceServers":[]}"#)
        do {
            _ = try await client.iceServers()
            XCTFail("expected noICEServers")
        } catch let error as APIError {
            XCTAssertEqual(error, .noICEServers)
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    // MARK: /api/translation/session

    func testCreateSessionEncodesSnakeCaseAndReturnsId() async throws {
        if let data = try? Fixtures.data("session_create") {
            StubProtocol.enqueue(data: data)
        } else {
            StubProtocol.enqueue(json: #"{"session_id":"GRC-abc","status":"waiting"}"#)
        }
        let id = try await client.createSession(CreateSessionRequest(callerName: "Person A", callerLanguage: "en", clientId: "cid-1"))
        XCTAssertFalse(id.isEmpty)
        let body = try lastRequestJSON()
        XCTAssertEqual(body["caller_name"] as? String, "Person A")
        XCTAssertEqual(body["caller_language"] as? String, "en")
        XCTAssertEqual(body["topic"] as? String, "Translation Session")
        XCTAssertEqual(body["client_id"] as? String, "cid-1")
        let req = try XCTUnwrap(StubProtocol.requests.last?.request)
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.url?.path, "/api/translation/session")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Content-Type"), "application/json")
    }

    // MARK: /api/translation/offer

    func testOfferEncodesAndDecodesAnswer() async throws {
        if let data = try? Fixtures.data("offer_answer") {
            StubProtocol.enqueue(data: data)
        } else {
            StubProtocol.enqueue(json: #"{"pc_id":"pc-a","sdp":"v=0 typ relay","type":"answer"}"#)
        }
        let answer = try await client.offer(OfferRequest(sessionId: "GRC-1", language: "en", name: "Person A", sdp: "v=0"))
        XCTAssertFalse(answer.pcId.isEmpty)
        XCTAssertEqual(answer.type, "answer")
        XCTAssertFalse(answer.sdp.isEmpty)
        let body = try lastRequestJSON()
        XCTAssertEqual(body["session_id"] as? String, "GRC-1")
        XCTAssertEqual(body["language"] as? String, "en")
        XCTAssertEqual(body["name"] as? String, "Person A")
        XCTAssertEqual(body["sdp"] as? String, "v=0")
        XCTAssertEqual(body["type"] as? String, "offer")
    }

    func testOffer402IsPaymentRequiredWithBalance() async throws {
        let balance = Fixtures.balanceJSON("balance_pro_exhausted")
        StubProtocol.enqueue(status: 402, json: #"{"error":"no_translation_credit","balance":\#(balance)}"#)
        do {
            _ = try await client.offer(OfferRequest(sessionId: "GRC-1", language: "en", name: "A", sdp: "v=0"))
            XCTFail("expected 402")
        } catch APIError.paymentRequired(let b) {
            let b = try XCTUnwrap(b)
            XCTAssertEqual(b.tier, .pro)
            XCTAssertEqual(b.secondsRemaining, 0)
            XCTAssertTrue(b.enforced)
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func testOffer402WithoutBalanceStillTyped() async {
        StubProtocol.enqueue(status: 402, json: #"{"error":"no_translation_credit"}"#)
        do {
            _ = try await client.offer(OfferRequest(sessionId: "s", language: "en", name: "A", sdp: "v=0"))
            XCTFail()
        } catch APIError.paymentRequired(let b) {
            XCTAssertNil(b)
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    // MARK: /api/translation/ptt

    func testPTTHoldAndRelease() async throws {
        StubProtocol.enqueue(json: #"{"ok":true,"state":"recording"}"#)
        let hold = try await client.ptt(pcId: "pc-a", action: .hold)
        XCTAssertNil(hold.flushedBytes)
        var body = try lastRequestJSON()
        XCTAssertEqual(body["pc_id"] as? String, "pc-a")
        XCTAssertEqual(body["action"] as? String, "hold")

        StubProtocol.enqueue(json: #"{"ok":true,"state":"released","flushed_bytes":16000}"#)
        let release = try await client.ptt(pcId: "pc-a", action: .release)
        XCTAssertEqual(release.flushedBytes, 16000)
        body = try lastRequestJSON()
        XCTAssertEqual(body["action"] as? String, "release")
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.path, "/api/translation/ptt")
    }

    func testPTT409IsHTTPError() async {
        StubProtocol.enqueue(status: 409, json: #"{"ok":false,"state":"translating","error":"turn in flight"}"#)
        do {
            _ = try await client.ptt(pcId: "pc-a", action: .hold)
            XCTFail()
        } catch APIError.http(let status, let message) {
            XCTAssertEqual(status, 409)
            XCTAssertEqual(message, "turn in flight")
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    // MARK: /api/translation/poll

    func testPollDecodesFixtureFrames() async throws {
        let frames: [Data]
        if let fixture = try? Fixtures.json("poll_events"), let raw = fixture["frames"] as? [[String: Any]] {
            frames = try raw.map { try JSONSerialization.data(withJSONObject: $0) }
        } else {
            frames = [
                Data(#"{"events":[{"type":"status","state":"listening"},{"type":"turn","speaker":"pc-a","original_lang":"en","original":"Hello","translated":"你好","speaker_name":"Person A"}],"closed":false}"#.utf8),
                Data(#"{"events":[{"type":"turn_failed","speaker":"pc-b"}],"closed":true}"#.utf8),
            ]
        }
        var turns = 0, failed = 0, closed = false
        for frame in frames {
            StubProtocol.enqueue(data: frame)
            let response = try await client.poll(sessionId: "GRC-1")
            turns += response.events.filter(\.isTurn).count
            failed += response.events.filter(\.isTurnFailed).count
            closed = closed || response.closed
        }
        XCTAssertGreaterThan(turns, 0)
        XCTAssertGreaterThan(failed, 0)
        XCTAssertTrue(closed)
        let req = try XCTUnwrap(StubProtocol.requests.last?.request)
        XCTAssertEqual(req.url?.path, "/api/translation/poll")
        XCTAssertEqual(req.url?.query, "session_id=GRC-1")
        if let fixture = try? Fixtures.json("poll_events"), let expected = fixture["expected"] as? [String: Any] {
            if let expectedTurns = expected["turns"] as? Int { XCTAssertEqual(turns, expectedTurns) }
        }
    }

    func testPollUnknownEventTypesAreKeptNotFatal() async throws {
        StubProtocol.enqueue(json: #"{"events":[{"type":"live","preview":"..."}],"closed":false}"#)
        let response = try await client.poll(sessionId: "s")
        XCTAssertEqual(response.events.count, 1)
        XCTAssertFalse(response.events[0].isTurn)
    }

    // MARK: /api/hangup

    func testHangupPostsPcIdAndNeverThrows() async throws {
        StubProtocol.enqueue(json: #"{"ok":true,"found":true}"#)
        await client.hangup(pcId: "pc-b")
        let body = try lastRequestJSON()
        XCTAssertEqual(body["pc_id"] as? String, "pc-b")
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.path, "/api/hangup")

        StubProtocol.enqueueFailure(.notConnectedToInternet)
        await client.hangup(pcId: "pc-b")   // must not throw or crash
    }

    // MARK: /api/entitlement*

    func testEntitlementQueryAndDecode() async throws {
        StubProtocol.enqueue(json: Fixtures.balanceJSON("balance_free_unenforced"))
        let balance = try await client.entitlement(subject: "cid 1")
        XCTAssertEqual(balance.tier, .free)
        XCTAssertFalse(balance.enforced)
        let req = try XCTUnwrap(StubProtocol.requests.last?.request)
        XCTAssertEqual(req.url?.path, "/api/entitlement")
        XCTAssertEqual(req.url?.query, "subject=cid%201")
    }

    func testActivateEncodesStoreReachable() async throws {
        StubProtocol.enqueue(json: Fixtures.balanceJSON("entitlement_activate_verified"))
        let balance = try await client.activate(ActivateRequest(subject: "cid", platform: .ios, receipt: "jws.x.y", storeReachable: true))
        XCTAssertEqual(balance.tier, .pro)
        XCTAssertEqual(balance.verified, true)
        let body = try lastRequestJSON()
        XCTAssertEqual(body["subject"] as? String, "cid")
        XCTAssertEqual(body["platform"] as? String, "ios")
        XCTAssertEqual(body["receipt"] as? String, "jws.x.y")
        XCTAssertEqual(body["store_reachable"] as? Bool, true)
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.path, "/api/entitlement/activate")
    }

    func testConsumeSendsTotalSecondsAndReturnsNilOnFailure() async throws {
        StubProtocol.enqueue(json: Fixtures.balanceJSON("balance_pro"))
        let balance = await client.consume(subject: "cid", sessionId: "GRC-1", seconds: 45)
        XCTAssertEqual(balance?.tier, .pro)
        let body = try lastRequestJSON()
        XCTAssertEqual(body["subject"] as? String, "cid")
        XCTAssertEqual(body["session_id"] as? String, "GRC-1")
        XCTAssertEqual(body["session_seconds"] as? Int, 45)
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.path, "/api/entitlement/consume")

        StubProtocol.enqueue(status: 500, json: #"{"error":"boom"}"#)
        let failed = await client.consume(subject: "cid", sessionId: "GRC-1", seconds: 60)
        XCTAssertNil(failed)
    }

    // MARK: history

    func testSessionsListAndDetail() async throws {
        StubProtocol.enqueue(json: #"{"sessions":[{"session_id":"GRC-1","caller_name":"Person A","topic":"T","lang":"en","status":"active","duration":12}]}"#)
        let list = try await client.sessions(clientId: "cid")
        XCTAssertEqual(list.count, 1)
        XCTAssertTrue(list[0].isLive)
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.query, "client_id=cid")

        if let data = try? Fixtures.data("session_detail") {
            StubProtocol.enqueue(data: data)
        } else {
            StubProtocol.enqueue(json: #"{"session_id":"GRC-1","caller_name":"Person A","lang":"en","topic":"T","status":"ended","transcript":[{"type":"turn","speaker":"pc-a","original_lang":"en","original":"Hi","translated":"嗨"}],"live_transcripts":{}}"#)
        }
        let fetched = try await client.session(id: "GRC-1")
        let detail = try XCTUnwrap(fetched)
        XCTAssertFalse(detail.sessionId.isEmpty)
        XCTAssertFalse(detail.transcript.isEmpty)
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.path, "/api/translation/session/GRC-1")

        StubProtocol.enqueue(status: 404, json: #"{"error":"not found"}"#)
        let missing = try await client.session(id: "nope")
        XCTAssertNil(missing)
    }

    // MARK: transport failures

    func testNetworkFailureIsTypedError() async {
        StubProtocol.enqueueFailure(.notConnectedToInternet)
        do {
            _ = try await client.poll(sessionId: "s")
            XCTFail()
        } catch let error as APIError {
            XCTAssertTrue(error.isNetwork)
            if case .network(let code, _) = error { XCTAssertEqual(code, URLError.notConnectedToInternet.rawValue) }
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func testMalformedBodyIsDecodingError() async {
        StubProtocol.enqueue(json: #"{"pc_id": 12}"#)
        do {
            _ = try await client.offer(OfferRequest(sessionId: "s", language: "en", name: "A", sdp: "v=0"))
            XCTFail()
        } catch APIError.decoding {
            // ok
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func testBaseURLWithPathPrefixIsPreserved() async throws {
        client = makeStubClient(base: "https://api.test/prefix/")
        StubProtocol.enqueue(json: #"{"events":[],"closed":false}"#)
        _ = try await client.poll(sessionId: "s")
        XCTAssertEqual(StubProtocol.requests.last?.request.url?.path, "/prefix/api/translation/poll")
    }

    // MARK: Balance decoding rules

    func testBalanceMissingEnforcedMeansEnforced() throws {
        let b = try decodeBalance(#"{"tier":"pro","seconds_remaining":10}"#)
        XCTAssertTrue(b.enforced)
        XCTAssertEqual(b.secondsRemaining, 10)
        XCTAssertNil(b.verified)
    }

    func testEveryBalanceFixtureDecodes() throws {
        for name in ["balance_free_unenforced", "balance_free_enforced", "balance_pro", "balance_pro_exhausted",
                     "balance_sandbox", "entitlement_activate_verified", "entitlement_activate_unverified"] {
            XCTAssertNoThrow(try decodeBalance(Fixtures.balanceJSON(name)), name)
        }
        XCTAssertTrue(try decodeBalance(Fixtures.balanceJSON("balance_sandbox")).sandboxOk)
    }
}
