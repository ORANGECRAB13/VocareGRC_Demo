import Foundation

/// Whether a language pair can be translated on-device right now (CONTRACT §4).
///   installed   — both directions ready, no network needed
///   supported    — a download away
///   unsupported  — will never work on this device
public enum PairStatus: String, Codable, Equatable, Sendable {
    case installed
    case supported
    case unsupported
}

/// The slice of the offline engine VocaKit's screens need (Home's offline
/// switch, Settings' language list). `VocaSession.OfflineEngine` satisfies it;
/// the app target adapts one to the other so VocaKit never imports VocaSession.
public protocol OfflineAvailability: Sendable {
    func pairStatus(_ a: String, _ b: String) async -> PairStatus
    /// Installs both directions. `false` means the user declined — not an error.
    func prepare(_ a: String, _ b: String) async throws -> Bool
}

/// No engine at all (tests, unsupported OS): every pair is `unsupported`.
public struct NoOfflineAvailability: OfflineAvailability {
    public init() {}
    public func pairStatus(_ a: String, _ b: String) async -> PairStatus { .unsupported }
    public func prepare(_ a: String, _ b: String) async throws -> Bool { false }
}
