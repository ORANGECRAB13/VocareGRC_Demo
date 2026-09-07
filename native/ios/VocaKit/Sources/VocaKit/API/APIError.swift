import Foundation

/// Typed failures from `VocaAPI`. Session view models switch on these to pick
/// the error screen; everything that is not a 402 or a transport failure ends
/// up in `.http`.
public enum APIError: Error, Equatable, Sendable {
    /// `/offer` returned 402 `no_translation_credit`. The balance is whatever
    /// the body carried (it may be absent on older servers).
    case paymentRequired(Balance?)
    /// Any other non-2xx status, with the server's `error` string when present.
    case http(status: Int, message: String?)
    /// The transport failed: offline, DNS, timeout, TLS. `code` is the URLError code.
    case network(code: Int, description: String)
    /// 2xx but the body did not decode as expected.
    case decoding(String)
    /// `/api/ice` returned an empty list — the relay-only session cannot connect.
    case noICEServers
    /// The base URL is not configured or the path could not be formed.
    case invalidURL

    public var isNetwork: Bool {
        if case .network = self { return true }
        return false
    }

    public var isPaymentRequired: Bool {
        if case .paymentRequired = self { return true }
        return false
    }
}

extension APIError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .paymentRequired: return "No translation credit left."
        case .http(let status, let message): return message ?? "Server error (\(status))."
        case .network(_, let description): return description
        case .decoding(let detail): return "Unexpected response: \(detail)"
        case .noICEServers: return "No ICE servers available."
        case .invalidURL: return "The API address is not configured."
        }
    }
}
