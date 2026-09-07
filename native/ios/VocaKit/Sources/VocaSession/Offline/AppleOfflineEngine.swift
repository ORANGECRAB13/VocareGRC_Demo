import AVFoundation
import Foundation
import Speech
import SwiftUI
import Translation
import UIKit
import VocaKit

// MARK: - Apple on-device engine (CONTRACT §4)
//
// STT: `SFSpeechRecognizer` with `requiresOnDeviceRecognition`, partials
//      streamed to the caller.
// MT:  Apple Translation. iOS 26 has the headless
//      `TranslationSession(installedSource:target:)`; below that the only way
//      to get a session is SwiftUI's `.translationTask`, so a hidden host view
//      is presented for the duration of the call. Downloads
//      (`prepareTranslation()`) only exist inside `.translationTask` on every
//      OS, and Apple is directional — `prepare` does both directions.
// TTS: `AVSpeechSynthesizer`, local voices only.
//
// None of this runs in the Simulator (Translation) or under XCTest (Speech
// needs a mic and entitlements); the view model tests use a fake engine.

@MainActor
public final class AppleOfflineEngine: OfflineEngine {
    public static let recognitionFinishTimeout: TimeInterval = 4

    /// Chinese must name a script for Apple; there is no Cantonese model at all.
    private static let identifierOverrides: [String: String] = ["zh": "zh-Hans"]

    private static func language(for code: String) -> Locale.Language {
        Locale.Language(identifier: identifierOverrides[code] ?? code)
    }

    private var recognition: Recognition?
    private let synthesizer = SpeechSynthesizer()

    public init() {}

    // MARK: Pair status

    public func pairStatus(_ a: String, _ b: String) async -> PairStatus {
        guard SessionLanguages.canTranslateOffline(a, b) else { return .unsupported }
        let availability = LanguageAvailability()
        let forward = await availability.status(from: Self.language(for: a), to: Self.language(for: b))
        let backward = await availability.status(from: Self.language(for: b), to: Self.language(for: a))
        return min(Self.map(forward), Self.map(backward))
    }

    private static func map(_ status: LanguageAvailability.Status) -> PairStatus {
        switch status {
        case .installed: return .installed
        case .supported: return .supported
        case .unsupported: return .unsupported
        @unknown default: return .unsupported
        }
    }

    // MARK: Prepare (download), both directions

    public func prepare(_ a: String, _ b: String) async throws -> Bool {
        guard SessionLanguages.canTranslateOffline(a, b) else { return false }
        for (source, target) in [(a, b), (b, a)] {
            let installed = await TranslationHost.run(
                source: Self.language(for: source), target: Self.language(for: target)
            ) { session in
                do {
                    try await session.prepareTranslation()
                    return true
                } catch {
                    // A cancelled sheet throws; that is the user declining.
                    return false
                }
            }
            if !installed { return false }
        }
        return true
    }

    // MARK: Translate

    public func translate(_ text: String, from: String, to: String) async throws -> String {
        let source = Self.language(for: from)
        let target = Self.language(for: to)
        if #available(iOS 26.0, *) {
            do {
                let session = try TranslationSession(installedSource: source, target: target)
                return try await session.translate(text).targetText
            } catch {
                throw OfflineEngineError.translationUnavailable(from: from, to: to)
            }
        }
        // iOS 18–25: a session only exists inside `.translationTask`.
        let result: Result<String, Error> = await TranslationHost.run(source: source, target: target) { session in
            do { return .success(try await session.translate(text).targetText) }
            catch { return .failure(error) }
        }
        switch result {
        case .success(let translated): return translated
        case .failure: throw OfflineEngineError.translationUnavailable(from: from, to: to)
        }
    }

    // MARK: Speech recognition

    public func startRecognition(locale: String, partial: @escaping @Sendable (String) -> Void) async throws {
        if let recognition {
            recognition.cancel()
            self.recognition = nil
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: locale)),
              recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else {
            throw OfflineEngineError.recognitionUnavailable(locale: locale)
        }
        guard await Self.requestSpeechAuthorization() == .authorized,
              await AVAudioApplication.requestRecordPermission() else {
            throw OfflineEngineError.permissionDenied
        }
        let recognition = Recognition(recognizer: recognizer, partial: partial)
        try recognition.start()
        self.recognition = recognition
    }

    public func finishRecognition() async throws -> String {
        guard let recognition else { return "" }
        self.recognition = nil
        return try await recognition.finish(timeout: Self.recognitionFinishTimeout)
    }

    public func cancelRecognition() async {
        recognition?.cancel()
        recognition = nil
    }

    private static func requestSpeechAuthorization() async -> SFSpeechRecognizerAuthorizationStatus {
        let current = SFSpeechRecognizer.authorizationStatus()
        if current != .notDetermined { return current }
        return await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
    }

    // MARK: Speech synthesis (local voices only)

    public func speak(_ text: String, locale: String) async throws {
        guard let voice = Self.localVoice(for: locale) else {
            throw OfflineEngineError.noLocalVoice(locale: locale)
        }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = voice
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        await synthesizer.speak(utterance)
    }

    /// Exact locale first, then language prefix. Every `AVSpeechSynthesisVoice`
    /// is an installed, on-device voice, so "local only" is the whole list.
    static func localVoice(for locale: String) -> AVSpeechSynthesisVoice? {
        let voices = AVSpeechSynthesisVoice.speechVoices()
        let normalise = { (value: String) in value.lowercased().replacingOccurrences(of: "_", with: "-") }
        let target = normalise(locale)
        if let exact = voices.first(where: { normalise($0.language) == target }) { return exact }
        let prefix = target.split(separator: "-").first.map(String.init) ?? target
        return voices.first(where: { normalise($0.language).split(separator: "-").first.map(String.init) == prefix })
    }
}

// MARK: - One recognition session

@MainActor
private final class Recognition {
    private let recognizer: SFSpeechRecognizer
    private let partial: @Sendable (String) -> Void
    private let audioEngine = AVAudioEngine()
    private let request = SFSpeechAudioBufferRecognitionRequest()
    private var task: SFSpeechRecognitionTask?
    private var latest = ""
    private var failure: Error?
    private var finalContinuation: CheckedContinuation<String, Error>?
    private var isFinal = false

    init(recognizer: SFSpeechRecognizer, partial: @escaping @Sendable (String) -> Void) {
        self.recognizer = recognizer
        self.partial = partial
    }

    func start() throws {
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        request.taskHint = .dictation

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker, .allowBluetooth])
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        let request = self.request
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        audioEngine.prepare()
        try audioEngine.start()

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor [weak self] in
                self?.handle(result: result, error: error)
            }
        }
    }

    private func handle(result: SFSpeechRecognitionResult?, error: Error?) {
        if let result {
            latest = result.bestTranscription.formattedString
            partial(latest)
            if result.isFinal {
                isFinal = true
                finalContinuation?.resume(returning: latest)
                finalContinuation = nil
            }
        }
        if let error, !isFinal {
            failure = error
            // A "no speech" cancellation still leaves whatever was heard.
            finalContinuation?.resume(returning: latest)
            finalContinuation = nil
        }
    }

    func finish(timeout: TimeInterval) async throws -> String {
        stopAudio()
        request.endAudio()
        if isFinal || failure != nil { return latest }

        let text: String = try await withCheckedThrowingContinuation { continuation in
            finalContinuation = continuation
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                guard let self, let pending = self.finalContinuation else { return }
                self.finalContinuation = nil
                // Timed out waiting for `isFinal`; the last partial is still
                // the best transcript we have.
                pending.resume(returning: self.latest)
            }
        }
        task?.cancel()
        task = nil
        return text
    }

    func cancel() {
        stopAudio()
        request.endAudio()
        task?.cancel()
        task = nil
        finalContinuation?.resume(throwing: OfflineEngineError.cancelled)
        finalContinuation = nil
    }

    private func stopAudio() {
        guard audioEngine.isRunning else { return }
        audioEngine.inputNode.removeTap(onBus: 0)
        audioEngine.stop()
    }
}

// MARK: - Synthesizer with async completion

@MainActor
private final class SpeechSynthesizer: NSObject, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var continuation: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ utterance: AVSpeechUtterance) async {
        if synthesizer.isSpeaking { synthesizer.stopSpeaking(at: .immediate) }
        await withCheckedContinuation { continuation in
            self.continuation = continuation
            synthesizer.speak(utterance)
        }
    }

    private func finished() {
        continuation?.resume()
        continuation = nil
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.finished() }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.finished() }
    }
}

// MARK: - Hidden `.translationTask` host
//
// `prepareTranslation()` (and, before iOS 26, any `TranslationSession` at all)
// is only reachable from the SwiftUI modifier. This presents a transparent
// hosting controller over the key window for the duration of one call and
// dismisses it afterwards. The controller must be in the window hierarchy for
// Apple's download sheet to appear, which is why it is presented rather than
// merely instantiated.

@MainActor
private enum TranslationHost {
    static func run<T: Sendable>(source: Locale.Language, target: Locale.Language,
                                 _ work: @escaping @Sendable (TranslationSession) async -> T) async -> T? {
        guard let presenter = topViewController() else { return nil }
        return await withCheckedContinuation { (continuation: CheckedContinuation<T?, Never>) in
            var host: UIViewController?
            var settled = false
            let finish: @MainActor (T?) -> Void = { value in
                guard !settled else { return }
                settled = true
                host?.dismiss(animated: false)
                host = nil
                continuation.resume(returning: value)
            }
            let view = TranslationHostView(source: source, target: target) { session in
                let value = await work(session)
                await finish(value)
            }
            let controller = UIHostingController(rootView: view)
            controller.view.backgroundColor = .clear
            controller.modalPresentationStyle = .overFullScreen
            controller.modalTransitionStyle = .crossDissolve
            host = controller
            presenter.present(controller, animated: false)
        }
    }

    /// Convenience for non-optional results: a missing presenter reads as "declined".
    static func run(source: Locale.Language, target: Locale.Language,
                    _ work: @escaping @Sendable (TranslationSession) async -> Bool) async -> Bool {
        let value: Bool? = await run(source: source, target: target, work)
        return value ?? false
    }

    static func run<T: Sendable>(source: Locale.Language, target: Locale.Language,
                                 _ work: @escaping @Sendable (TranslationSession) async -> Result<T, Error>) async -> Result<T, Error> {
        let value: Result<T, Error>? = await run(source: source, target: target, work)
        return value ?? .failure(OfflineEngineError.translationFailed("No window to host the translation session."))
    }

    private static func topViewController() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let window = scenes.flatMap(\.windows).first { $0.isKeyWindow } ?? scenes.flatMap(\.windows).first
        var top = window?.rootViewController
        while let presented = top?.presentedViewController { top = presented }
        return top
    }
}

private struct TranslationHostView: View {
    let source: Locale.Language
    let target: Locale.Language
    let work: @Sendable (TranslationSession) async -> Void

    @State private var configuration: TranslationSession.Configuration?

    var body: some View {
        Color.clear
            .translationTask(configuration) { session in
                // `TranslationSession` is not Sendable and SwiftUI hands it to us on
                // the main actor; the work closure awaits it on the cooperative pool.
                // Only one closure ever touches the value, so the hop is safe.
                nonisolated(unsafe) let handed = session
                await work(handed)
            }
            .onAppear {
                configuration = TranslationSession.Configuration(source: source, target: target)
            }
    }
}

// MARK: - PairStatus ordering (worse-of-two-directions)

extension PairStatus: Comparable {
    private var rank: Int {
        switch self {
        case .unsupported: return 0
        case .supported: return 1
        case .installed: return 2
        }
    }

    public static func < (lhs: PairStatus, rhs: PairStatus) -> Bool { lhs.rank < rhs.rank }
}
