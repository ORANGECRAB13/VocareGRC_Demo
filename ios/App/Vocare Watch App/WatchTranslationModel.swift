import Foundation
import Combine
import WatchKit

struct WatchConversationTurn: Identifiable, Codable {
    let id: UUID
    let speaker: String
    let original: String
    let translated: String
    let sourceLanguage: String
    let targetLanguage: String

    init(
        id: UUID = UUID(), speaker: String, original: String, translated: String,
        sourceLanguage: String, targetLanguage: String
    ) {
        self.id = id
        self.speaker = speaker
        self.original = original
        self.translated = translated
        self.sourceLanguage = sourceLanguage
        self.targetLanguage = targetLanguage
    }
}

struct WatchConversationSession {
    let id = "WATCH-\(UUID().uuidString)"
    let startedAt = Date()
    var turns: [WatchConversationTurn] = []

    func handoffPayload(status: String) -> [String: Any] {
        let transcriptObjects: [[String: Any]] = turns.map { turn in
            [
                "speaker_name": turn.speaker,
                "original": turn.original,
                "translated": turn.translated,
                "original_lang": turn.sourceLanguage,
                "translated_lang": turn.targetLanguage,
            ]
        }
        let transcript = (try? JSONSerialization.data(withJSONObject: transcriptObjects))
            .flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
        return [
            "sessionId": id,
            "callerName": "Apple Watch",
            "topic": "Watch Translation",
            "status": status,
            "languageA": turns.first?.sourceLanguage ?? "en",
            "languageB": turns.first?.targetLanguage ?? "zh",
            "startedAt": ISO8601DateFormatter().string(from: startedAt),
            "durationSeconds": max(0, Int(Date().timeIntervalSince(startedAt))),
            "transcriptJSON": transcript,
        ]
    }
}

@MainActor
final class WatchTranslationModel: ObservableObject {
    enum Phase { case ready, recording, translating, output, session, error }

    @Published var phase: Phase = .ready
    @Published var sourceLanguage = "en"
    @Published var targetLanguage = "zh"
    @Published var turns: [WatchConversationTurn] = []
    @Published var errorMessage = ""

    private let audio = WatchAudioController()
    private let client = WatchTranslationClient()
    private var lastAudio: Data?
    private var conversation = WatchConversationSession()
    private var recordingStartTask: Task<Void, Error>?
    private var recordingTimeoutTask: Task<Void, Never>?
    private var wearerLanguage: String?
    /// Last pipeline step reached, surfaced on the error screen so a stall can be
    /// located without attaching a debugger to the watch.
    private var stage = "idle"

    var sourceLabel: String { sourceLanguage.uppercased() }
    var targetLabel: String { targetLanguage.uppercased() }
    var latestTurn: WatchConversationTurn? { turns.last }
    var elapsed: TimeInterval { Date().timeIntervalSince(conversation.startedAt) }

    init() {
        _ = WatchPhoneHandoff.shared
        audio.onPlaybackFinished = {
            Task { @MainActor in
                WKInterfaceDevice.current().play(.directionUp)
            }
        }
    }

    func beginHold() {
        guard phase == .ready else { return }
        if turns.isEmpty {
            conversation = WatchConversationSession()
        }
        phase = .recording
        stage = "mic-permission"
        WKInterfaceDevice.current().play(.click)
        recordingStartTask = Task { [weak self] in
            try await self?.audio.startRecording()
            await MainActor.run { self?.stage = "recording" }
        }
        recordingTimeoutTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(90))
            guard !Task.isCancelled else { return }
            self?.releaseHold()
        }
        Task { [weak self] in
            do { try await self?.recordingStartTask?.value }
            catch { self?.fail(error) }
        }
    }

    func releaseHold() {
        guard phase == .recording else { return }
        recordingTimeoutTask?.cancel()
        recordingTimeoutTask = nil
        WKInterfaceDevice.current().play(.click)
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(120))
            WKInterfaceDevice.current().play(.click)
        }
        phase = .translating
        let startTask = recordingStartTask
        recordingStartTask = nil
        let watchdog = Task { [weak self] in
            try? await Task.sleep(for: .seconds(75))
            guard !Task.isCancelled, let self, self.phase == .translating else { return }
            NSLog("[Watch] translation watchdog fired — stalled at %@", self.stage)
            self.fail(WatchTranslationClient.ClientError.server("Stalled at: \(self.stage)"))
        }
        Task { [weak self] in
            guard let self else { return }
            defer { watchdog.cancel() }
            do {
                stage = "await-recorder"
                NSLog("[Watch] release: awaiting recorder start")
                try await startTask?.value
                stage = "stop-recording"
                let recording = try audio.stopRecording()
                stage = "uploading(\(recording.count)B)"
                NSLog("[Watch] release: recorded %d bytes, sending", recording.count)
                let result = try await client.translate(
                    wavData: recording, source: sourceLanguage, target: targetLanguage
                )
                stage = "server-ok"
                NSLog("[Watch] release: server responded ok")
                if wearerLanguage == nil { wearerLanguage = sourceLanguage }
                let turn = WatchConversationTurn(
                    speaker: sourceLanguage == wearerLanguage ? "You" : "Other person",
                    original: result.original, translated: result.translated,
                    sourceLanguage: result.sourceLanguage, targetLanguage: result.targetLanguage
                )
                turns.append(turn)
                conversation.turns = turns
                WatchPhoneHandoff.shared.transfer(session: conversation, status: "active")
                lastAudio = Data(base64Encoded: result.audioWavBase64)
                phase = .output
                try? playLastTranslation()
            } catch {
                NSLog("[Watch] release failed: %@", error.localizedDescription)
                fail(error)
            }
        }
    }

    func playLastTranslation() throws {
        guard let lastAudio else { return }
        try audio.play(wavData: lastAudio)
    }

    func reply() {
        // Direction is never swapped automatically: the same speaker may take
        // several turns in a row. The crown swaps languages whenever wanted.
        audio.stopPlayback()
        phase = .ready
    }

    func retry() {
        audio.stopPlayback()
        phase = .ready
    }

    func showSession() { phase = .session }
    func dismissSession() { phase = latestTurn == nil ? .ready : .output }

    func swapLanguages() {
        guard phase == .ready || phase == .output else { return }
        swap(&sourceLanguage, &targetLanguage)
        WKInterfaceDevice.current().play(.directionUp)
    }

    func endSession() {
        WatchPhoneHandoff.shared.transfer(session: conversation, status: "ended")
        turns.removeAll()
        conversation = WatchConversationSession()
        lastAudio = nil
        wearerLanguage = nil
        phase = .ready
        WKInterfaceDevice.current().play(.stop)
    }

    private func fail(_ error: Error) {
        audio.cancelRecording()
        errorMessage = "[\(stage)] \(error.localizedDescription)"
        phase = .error
        WKInterfaceDevice.current().play(.failure)
    }
}
