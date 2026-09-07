import Foundation
import VocaKit

// MARK: - On-device pipeline seams (CONTRACT §4 / §6)
//
// Speech recognition is a hold-and-release affair, so it is two calls: start
// listening on hold, finish (and get the transcript) on release. Everything
// else is one call per stage. `AppleOfflineEngine` is the real thing; tests
// use a scripted fake.

public protocol OfflineEngine: Sendable {
    /// `installed | supported | unsupported` for the pair, both directions.
    func pairStatus(_ a: String, _ b: String) async -> PairStatus

    /// Install the language packs for both directions. `false` means the user
    /// declined the download — that is not an error.
    func prepare(_ a: String, _ b: String) async throws -> Bool

    /// Begin on-device recognition in `locale` (e.g. `en-US`). Returns once the
    /// recogniser is listening. `partial` receives the running transcript.
    func startRecognition(locale: String, partial: @escaping @Sendable (String) -> Void) async throws

    /// Stop listening and return the final transcript (may be empty).
    func finishRecognition() async throws -> String

    /// Abort a recognition in progress (teardown).
    func cancelRecognition() async

    /// Translate on device. Throws when the pair is missing or unsupported.
    func translate(_ text: String, from: String, to: String) async throws -> String

    /// Speak with a local voice for `locale`; returns when playback finishes.
    /// Throws when no local voice is installed.
    func speak(_ text: String, locale: String) async throws
}

public enum OfflineEngineError: Error, Equatable, Sendable {
    /// Speech recognition or microphone permission refused.
    case permissionDenied
    /// No on-device recogniser for this locale.
    case recognitionUnavailable(locale: String)
    case recognitionFailed(String)
    case recognitionTimedOut
    /// No installed language pack for `from → to`.
    case translationUnavailable(from: String, to: String)
    case translationFailed(String)
    /// No local voice for this locale.
    case noLocalVoice(locale: String)
    case cancelled
}
