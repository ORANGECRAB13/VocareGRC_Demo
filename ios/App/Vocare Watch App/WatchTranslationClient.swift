import Foundation

struct WatchTranslationResult: Decodable {
    let original: String
    let translated: String
    let sourceLanguage: String
    let targetLanguage: String
    let audioWavBase64: String

    enum CodingKeys: String, CodingKey {
        case original, translated
        case sourceLanguage = "source_language"
        case targetLanguage = "target_language"
        case audioWavBase64 = "audio_wav_base64"
    }
}

struct WatchTranslationClient {
    private let endpoint = URL(string: "https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/api/watch/translate")!

    /// Fails instead of parking the request when the watch has no route to the
    /// internet, so the UI reports "no connection" rather than hanging.
    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 65
        config.timeoutIntervalForResource = 70
        config.allowsExpensiveNetworkAccess = true
        config.allowsConstrainedNetworkAccess = true
        return URLSession(configuration: config)
    }()

    func translate(wavData: Data, source: String, target: String) async throws -> WatchTranslationResult {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        // Keep the client deadline beyond the backend's bounded 55-second Agent session.
        request.timeoutInterval = 65
        request.httpBody = wavData
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.setValue(source, forHTTPHeaderField: "X-Vocare-Source-Language")
        request.setValue(target, forHTTPHeaderField: "X-Vocare-Target-Language")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await Self.session.data(for: request)
        } catch let error as URLError {
            throw ClientError.server(Self.networkMessage(for: error))
        }
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw ClientError.server(Self.errorMessage(from: data))
        }
        return try JSONDecoder().decode(WatchTranslationResult.self, from: data)
    }

    private static func networkMessage(for error: URLError) -> String {
        switch error.code {
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
            return "Watch has no internet. Keep it near the unlocked iPhone."
        case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed:
            return "Cannot reach the translation server."
        case .timedOut:
            return "Upload timed out — connection too slow."
        default:
            return "Network error (\(error.code.rawValue))."
        }
    }

    private static func errorMessage(from data: Data) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in ["error", "detail"] {
                if let message = object[key] as? String, !message.isEmpty {
                    return message
                }
            }
        }
        let text = String(data: data, encoding: .utf8) ?? ""
        return text.isEmpty ? "Translation failed." : text
    }

    enum ClientError: LocalizedError {
        case server(String)
        var errorDescription: String? {
            if case let .server(message) = self { return message }
            return "Translation failed"
        }
    }
}
