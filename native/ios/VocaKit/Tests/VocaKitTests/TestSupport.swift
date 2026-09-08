import Foundation
import XCTest
@testable import VocaKit

// MARK: - URLProtocol stub

/// Captures every request and answers from a queue of canned responses.
final class StubProtocol: URLProtocol {
    struct Stub {
        var status: Int
        var body: Data
        var error: URLError?
    }

    nonisolated(unsafe) static var queue: [Stub] = []
    nonisolated(unsafe) static var requests: [(request: URLRequest, body: Data?)] = []
    private static let lock = NSLock()

    static func reset() {
        lock.lock(); defer { lock.unlock() }
        queue = []
        requests = []
    }

    static func enqueue(status: Int = 200, json: String) {
        lock.lock(); defer { lock.unlock() }
        queue.append(Stub(status: status, body: Data(json.utf8)))
    }

    static func enqueue(status: Int = 200, data: Data) {
        lock.lock(); defer { lock.unlock() }
        queue.append(Stub(status: status, body: data))
    }

    static func enqueueFailure(_ code: URLError.Code) {
        lock.lock(); defer { lock.unlock() }
        queue.append(Stub(status: 0, body: Data(), error: URLError(code)))
    }

    static func next() -> Stub? {
        lock.lock(); defer { lock.unlock() }
        return queue.isEmpty ? nil : queue.removeFirst()
    }

    static func record(_ request: URLRequest, body: Data?) {
        lock.lock(); defer { lock.unlock() }
        requests.append((request, body))
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let body = request.httpBody ?? request.httpBodyStream.map { stream -> Data in
            stream.open()
            defer { stream.close() }
            var data = Data()
            let bufferSize = 4096
            let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
            defer { buffer.deallocate() }
            while stream.hasBytesAvailable {
                let read = stream.read(buffer, maxLength: bufferSize)
                if read <= 0 { break }
                data.append(buffer, count: read)
            }
            return data
        }
        Self.record(request, body: body)
        guard let stub = Self.next() else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        if let error = stub.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: stub.status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: stub.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

func makeStubClient(base: String = "https://api.test") -> LiveAPIClient {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [StubProtocol.self]
    return LiveAPIClient(baseURL: URL(string: base)!, session: URLSession(configuration: config))
}

func lastRequestJSON() throws -> [String: Any] {
    guard let body = StubProtocol.requests.last?.body else { return [:] }
    return try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
}

// MARK: - Fixtures (native/fixtures/*.json when present, else inline equivalents)

enum Fixtures {
    static let root: URL? = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { url.deleteLastPathComponent() }   // Tests/VocaKitTests/File.swift -> native/
        let candidate = url.appendingPathComponent("fixtures")
        return FileManager.default.fileExists(atPath: candidate.path) ? candidate : nil
    }()

    static var available: Bool { root != nil }

    static func data(_ name: String) throws -> Data {
        guard let root else { throw XCTSkip("native/fixtures not present") }
        return try Data(contentsOf: root.appendingPathComponent("\(name).json"))
    }

    static func json(_ name: String) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data(name)) as? [String: Any])
    }

    /// A balance body: from the fixture if present, else an inline equivalent.
    static func balanceJSON(_ name: String) -> String {
        if let data = try? data(name), let text = String(data: data, encoding: .utf8) { return text }
        switch name {
        case "balance_pro":
            return #"{"tier":"pro","period":"2026-09","seconds_total":3600,"seconds_used":900,"seconds_remaining":2700,"durable":true,"enforced":true,"sandbox_ok":false}"#
        case "balance_pro_exhausted":
            return #"{"tier":"pro","period":"2026-09","seconds_total":3600,"seconds_used":3600,"seconds_remaining":0,"durable":true,"enforced":true,"sandbox_ok":false}"#
        case "balance_free_enforced":
            return #"{"tier":"free","period":"2026-09","seconds_total":0,"seconds_used":0,"seconds_remaining":0,"durable":true,"enforced":true,"sandbox_ok":false}"#
        case "balance_sandbox":
            return #"{"tier":"free","period":"2026-09","seconds_total":0,"seconds_used":0,"seconds_remaining":0,"durable":false,"enforced":false,"sandbox_ok":true}"#
        case "entitlement_activate_verified":
            return #"{"tier":"pro","period":"2026-09","seconds_total":3600,"seconds_used":0,"seconds_remaining":3600,"durable":true,"enforced":true,"sandbox_ok":false,"verified":true}"#
        case "entitlement_activate_unverified":
            return #"{"tier":"free","period":"2026-09","seconds_total":0,"seconds_used":0,"seconds_remaining":0,"durable":true,"enforced":true,"sandbox_ok":false,"verified":false}"#
        default: // balance_free_unenforced — current production
            return #"{"tier":"free","period":"2026-09","seconds_total":0,"seconds_used":0,"seconds_remaining":0,"durable":true,"enforced":false,"sandbox_ok":false}"#
        }
    }
}

// MARK: - Fake store

final class FakeStore: StoreClient, @unchecked Sendable {
    var product: StoreProduct? = StoreProduct(id: "vocare_pro_monthly", displayPrice: "A$29.99", displayName: "Voca Pro", description: "60 minutes")
    var purchaseOutcome: StorePurchaseOutcome = .cancelled
    var purchaseError: StoreError?
    var held: StoreEntitlement?
    /// Non-nil = the store could not be asked at all.
    var entitlementError: StoreError?
    var restored: StoreEntitlement?
    var purchaseCalls = 0
    var restoreCalls = 0

    func product(id: String) async throws -> StoreProduct {
        guard let product else { throw StoreError.productNotFound }
        return product
    }
    func purchase(id: String) async throws -> StorePurchaseOutcome {
        purchaseCalls += 1
        if let purchaseError { throw purchaseError }
        return purchaseOutcome
    }
    func currentEntitlement(id: String) async throws -> StoreEntitlement? {
        if let entitlementError { throw entitlementError }
        return held
    }
    func restore(id: String) async -> StoreEntitlement? {
        restoreCalls += 1
        return restored ?? held
    }
    func updates() -> AsyncStream<StoreUpdate> { AsyncStream { $0.finish() } }
}

// MARK: - Fake entitlement API

final class FakeEntitlementAPI: VocaEntitlementAPI, @unchecked Sendable {
    var balance: Balance
    var activateResponse: Balance?
    var error: APIError?
    var activateRequests: [ActivateRequest] = []
    var entitlementCalls = 0

    init(balance: Balance) { self.balance = balance }

    func entitlement(subject: String) async throws -> Balance {
        entitlementCalls += 1
        if let error { throw error }
        return balance
    }

    func activate(_ req: ActivateRequest) async throws -> Balance {
        activateRequests.append(req)
        if let error { throw error }
        return activateResponse ?? balance
    }
}

@MainActor
func makePreferences() -> Preferences {
    let suite = "vocare.tests.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    return Preferences(defaults: defaults)
}

func decodeBalance(_ json: String) throws -> Balance {
    try JSONDecoder().decode(Balance.self, from: Data(json.utf8))
}
