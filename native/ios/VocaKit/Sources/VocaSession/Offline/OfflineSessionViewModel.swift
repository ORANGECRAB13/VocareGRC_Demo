import Foundation
import VocaKit

// MARK: - Offline session (CONTRACT §4, mirrors JSX `OfflineLiveScreen`)
//
// Zero network. Each hold-and-release on side S runs STT → translate → TTS
// through the injected `OfflineEngine`, appending one turn. The same panel
// semantics as the cloud session: a turn spoken by A is "You said" on A's
// panel and "Translated" on B's.

@MainActor
public final class OfflineSessionViewModel: ObservableObject {
    @Published public private(set) var state: LiveState

    public static let badge = "On device"

    public enum Phase: Equatable, Sendable {
        case idle, listening, translating, speaking
    }

    public struct OfflineTurn: Equatable, Sendable {
        public let side: Side
        public let from: String
        public let to: String
        public let original: String
        public let translated: String
    }

    /// One hold-and-release. Identity-compared so a stale operation that wakes
    /// up after `end()` (or after an error reset) cannot touch state.
    @MainActor private final class Operation {
        let side: Side
        var released = false
        var start: Task<Void, Error>?
        init(side: Side) { self.side = side }
    }

    private let config: SessionConfig
    private let engine: OfflineEngine
    private let scheduler: SessionScheduler
    private let tasks = TaskTracker()

    private var langA: String
    private var langB: String
    private(set) public var turns: [OfflineTurn] = []
    private var holding: Side?
    private(set) public var phase: Phase = .idle
    private var error: String?
    private var partial = ""
    private var elapsedSeconds = 0
    private var ended = false
    private var operation: Operation?
    private var ticker: SchedulerToken?

    public init(config: SessionConfig, engine: OfflineEngine, scheduler: SessionScheduler = TaskScheduler()) {
        self.config = config
        self.engine = engine
        self.scheduler = scheduler
        self.langA = config.langA
        self.langB = config.langB
        self.state = LiveState(
            sideA: SideState(name: config.nameA, langCode: config.langA, langLabel: SessionLanguages.label(config.langA)),
            sideB: SideState(name: config.nameB, langCode: config.langB, langLabel: SessionLanguages.label(config.langB)),
            phase: .live, badge: OfflineSessionViewModel.badge
        )
        ticker = scheduler.repeating(every: 1) { [weak self] in
            guard let self, !self.ended else { return }
            self.elapsedSeconds += 1
            self.publish()
        }
        publish()
    }

    /// Await in-flight hold/release work (tests).
    public func settle() async { await tasks.settle() }

    // MARK: Push-to-talk

    public func hold(side: Side) {
        guard !ended, operation == nil, phase == .idle else { return }
        let op = Operation(side: side)
        operation = op
        error = nil
        partial = ""
        holding = side
        phase = .listening
        publish()

        let from = side == .a ? langA : langB
        let a = langA, b = langB
        let engine = self.engine
        op.start = Task { @MainActor [weak self] in
            // §4: packs are installable in-app. A `supported` pair is
            // downloaded here (Apple's own sheet asks the user); a declined
            // download is a notice, not a hard failure.
            switch await engine.pairStatus(a, b) {
            case .installed:
                break
            case .supported:
                guard try await engine.prepare(a, b) else {
                    throw OfflineEngineError.translationUnavailable(from: a, to: b)
                }
            case .unsupported:
                throw OfflineEngineError.translationUnavailable(from: a, to: b)
            }
            guard let self, self.operation === op else { throw OfflineEngineError.cancelled }
            try await engine.startRecognition(locale: SessionLanguages.speechLocale(from)) { text in
                Task { @MainActor [weak self] in
                    guard let self, self.operation === op, self.holding == side else { return }
                    self.partial = text
                    self.publish()
                }
            }
        }
        tasks.track { [weak self] in
            guard let self else { return }
            do {
                try await op.start?.value
            } catch {
                guard self.operation === op else { return }
                self.operation = nil
                self.holding = nil
                self.phase = .idle
                self.error = OfflineSessionViewModel.message(for: error, from: from, to: side == .a ? b : a,
                                                             fallback: "Could not start on-device speech recognition.")
                self.publish()
            }
        }
    }

    public func release(side: Side) {
        guard let op = operation, op.side == side, !op.released else { return }
        op.released = true
        let from = side == .a ? langA : langB
        let to = side == .a ? langB : langA
        holding = nil
        phase = .translating
        publish()

        tasks.track { [weak self] in
            guard let self else { return }

            // 1. Finish recognition.
            var heard = ""
            do {
                try await op.start?.value
                guard self.operation === op else { return }
                heard = try await self.engine.finishRecognition()
                guard self.operation === op else { return }
            } catch {
                guard self.operation === op else { return }
                self.finishOperation(error: OfflineSessionViewModel.message(
                    for: error, from: from, to: to, fallback: "Could not finish speech recognition."))
                return
            }
            heard = heard.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !heard.isEmpty else {
                self.finishOperation(error:
                    "Nothing was recognised in \(SessionLanguages.label(from)). "
                    + "On-device recognition for \(SessionLanguages.label(from)) may not be installed — "
                    + "download the recognition language in your device speech settings.")
                return
            }

            // 2. Translate.
            self.phase = .translating
            self.publish()
            let translated = (try? await self.engine.translate(heard, from: from, to: to))?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            guard self.operation === op else { return }
            guard !translated.isEmpty else {
                self.finishOperation(error:
                    "Could not translate \(SessionLanguages.label(from)) to \(SessionLanguages.label(to)) on this device. "
                    + "The language may not be downloaded — check Settings.")
                return
            }

            self.turns.append(OfflineTurn(side: side, from: from, to: to, original: heard, translated: translated))
            self.partial = ""
            self.phase = .speaking
            self.publish()

            // 3. Speak with a local voice.
            do {
                try await self.engine.speak(translated, locale: SessionLanguages.speechLocale(to))
            } catch {
                if self.operation === op {
                    self.error = OfflineSessionViewModel.message(
                        for: error, from: from, to: to, fallback: "Could not speak the translation offline.")
                }
            }
            guard self.operation === op else { return }
            self.finishOperation(error: self.error)
        }
    }

    private func finishOperation(error: String?) {
        operation = nil
        holding = nil
        phase = .idle
        partial = ""
        self.error = error
        publish()
    }

    /// Swap A/B languages between turns. Earlier bubbles were authored under
    /// the old assignment, so they are cleared (JSX `swapLanguages`).
    public func swapLanguages() {
        guard phase == .idle, operation == nil else { return }
        swap(&langA, &langB)
        turns.removeAll()
        partial = ""
        error = nil
        publish()
    }

    public func end() {
        guard !ended else { return }
        ended = true
        ticker?.cancel(); ticker = nil
        let op = operation
        operation = nil
        holding = nil
        phase = .idle
        let engine = self.engine
        if op != nil {
            tasks.track { await engine.cancelRecognition() }
        }
        state.phase = .ended(.user)
    }

    // MARK: Projection

    private static func message(for error: Error, from: String, to: String, fallback: String) -> String {
        switch error as? OfflineEngineError {
        case .permissionDenied:
            return "Speech recognition permission was declined. Enable microphone access for Voca in your device settings."
        case .recognitionUnavailable:
            return "On-device speech recognition is not available on this device."
        case .recognitionTimedOut:
            return "Speech recognition timed out. Please try again."
        case .translationUnavailable:
            return "Download both translation languages before starting an offline session."
        case .noLocalVoice:
            return "No offline voice is installed for this language. Download a voice in your device text-to-speech settings."
        case .recognitionFailed(let text), .translationFailed(let text):
            return text
        case .cancelled, .none:
            return fallback
        }
    }

    private func publish() {
        guard !ended else { return }
        let busy = phase != .idle

        func side(_ side: Side) -> SideState {
            let name = side == .a ? config.nameA : config.nameB
            let otherName = side == .a ? config.nameB : config.nameA
            let code = side == .a ? langA : langB
            let status: String
            if holding == side { status = "Recording" }
            else if phase == .translating { status = "Translating..." }
            else if phase == .speaking { status = "Speaking..." }
            else if holding != nil { status = "Listening to \(otherName)" }
            else { status = turns.isEmpty ? "Ready" : "Ready - speak again" }

            let views = turns.enumerated().map { index, turn in
                TurnView(id: index, original: turn.original, translated: turn.translated, mine: turn.side == side)
            }
            return SideState(
                name: name, langCode: code, langLabel: SessionLanguages.label(code),
                pressing: holding == side,
                disabled: busy && holding != side,
                dimmed: busy && holding != side,
                status: status,
                turns: views,
                note: (holding == side && !partial.isEmpty) ? partial : nil
            )
        }

        state = LiveState(
            sideA: side(.a), sideB: side(.b),
            elapsed: TimeInterval(elapsedSeconds),
            phase: .live,
            badge: OfflineSessionViewModel.badge,
            notice: error
        )
    }
}
