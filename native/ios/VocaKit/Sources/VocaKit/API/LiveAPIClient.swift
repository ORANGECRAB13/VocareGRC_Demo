import Foundation

/// The HTTP client. Everything the JSX `api()` helpers do, on `URLSession`.
/// Transport is injectable (a `URLSessionConfiguration` with a `URLProtocol`
/// stub in tests) so every endpoint's encoding is verifiable offline.
public final class LiveAPIClient: VocaFullAPI, @unchecked Sendable {
    public static let productionBase = URL(string: "https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io")!

    public let baseURL: URL
    private let session: URLSession
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    public init(baseURL: URL = LiveAPIClient.productionBase, session: URLSession? = nil) {
        self.baseURL = baseURL
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = 20
            config.waitsForConnectivity = false
            self.session = URLSession(configuration: config)
        }
    }

    /// Reads `VOCARE_API_BASE` from the app's Info.plist, falling back to production.
    public static func fromBundle(_ bundle: Bundle = .main) -> LiveAPIClient {
        if let raw = bundle.object(forInfoDictionaryKey: "VOCARE_API_BASE") as? String,
           let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)), url.host != nil {
            return LiveAPIClient(baseURL: url)
        }
        return LiveAPIClient()
    }

    // MARK: VocaAPI

    public func iceServers() async throws -> [ICEServer] {
        let response: ICEServersResponse = try await get("/api/ice")
        guard !response.iceServers.isEmpty else { throw APIError.noICEServers }
        return response.iceServers
    }

    public func createSession(_ req: CreateSessionRequest) async throws -> String {
        let response: CreateSessionResponse = try await post("/api/translation/session", body: req)
        return response.sessionId
    }

    public func offer(_ req: OfferRequest) async throws -> OfferAnswer {
        try await post("/api/translation/offer", body: req)
    }

    public func ptt(pcId: String, action: PTTAction) async throws -> PTTResult {
        try await post("/api/translation/ptt", body: PTTRequest(pcId: pcId, action: action))
    }

    public func poll(sessionId: String) async throws -> PollResponse {
        try await get("/api/translation/poll", query: [URLQueryItem(name: "session_id", value: sessionId)])
    }

    public func hangup(pcId: String) async {
        guard !pcId.isEmpty else { return }
        let _: HangupResponse? = try? await post("/api/hangup", body: HangupRequest(pcId: pcId))
    }

    public func consume(subject: String, sessionId: String, seconds: Int) async -> Balance? {
        let body = ConsumeRequest(subject: subject, sessionId: sessionId, sessionSeconds: max(0, seconds))
        return try? await post("/api/entitlement/consume", body: body)
    }

    // MARK: VocaEntitlementAPI

    public func entitlement(subject: String) async throws -> Balance {
        try await get("/api/entitlement", query: [URLQueryItem(name: "subject", value: subject)])
    }

    public func activate(_ req: ActivateRequest) async throws -> Balance {
        try await post("/api/entitlement/activate", body: req)
    }

    // MARK: VocaHistoryAPI

    public func sessions(clientId: String) async throws -> [SessionSummary] {
        let response: SessionListResponse = try await get("/api/translation/sessions",
                                                          query: [URLQueryItem(name: "client_id", value: clientId)])
        return response.sessions
    }

    public func session(id: String) async throws -> SessionDetail? {
        do {
            return try await get("/api/translation/session/\(id)")
        } catch APIError.http(let status, _) where status == 404 {
            return nil
        }
    }

    // MARK: Transport

    private func url(_ path: String, query: [URLQueryItem] = []) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw APIError.invalidURL }
        let basePath = components.path.hasSuffix("/") ? String(components.path.dropLast()) : components.path
        components.path = basePath + path
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw APIError.invalidURL }
        return url
    }

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        var request = URLRequest(url: try url(path, query: query))
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await send(request)
    }

    private func post<T: Decodable, B: Encodable>(_ path: String, body: B) async throws -> T {
        var request = URLRequest(url: try url(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try encoder.encode(body)
        return try await send(request)
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError {
            throw APIError.network(code: error.errorCode, description: error.localizedDescription)
        } catch {
            throw APIError.network(code: -1, description: error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.decoding("not an HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = try? decoder.decode(ErrorBody.self, from: data)
            if http.statusCode == 402 {
                throw APIError.paymentRequired(body?.balance)
            }
            throw APIError.http(status: http.statusCode, message: body?.error)
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error))
        }
    }
}
