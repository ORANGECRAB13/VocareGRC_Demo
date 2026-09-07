import Foundation
import VocaKit
@testable import VocaSession

// MARK: - Fake API
//
// Every call is recorded; responses are scripted per call. Poll frames are a
// FIFO so a test can replay `native/fixtures/poll_events.json` frame by frame.

@MainActor
final class FakeAPI: VocaAPI {
    enum Call: Equatable {
        case iceServers
        case createSession(callerLanguage: String)
        case offer(language: String)
        case ptt(pcId: String, action: PTTAction)
        case poll
        case hangup(pcId: String)
        case consume(sessionId: String, seconds: Int)
    }

    var calls: [Call] = []

    var iceServersResult: [ICEServer] = [ICEServer(urls: ["turn:example.test:3478"], username: "u", credential: "c")]
    var iceServersError: Error?
    var sessionId = "sess-1"
    var pcIds = ["pc-a", "pc-b"]
    var offerError: Error?
    /// Scripted per (pcId, action). Missing = hold → `nil`, release → 1200 bytes.
    var pttResults: [String: PTTResult] = [:]
    var pttErrors: [String: Error] = [:]
    var pollFrames: [PollResponse] = []
    var pollError: Error?
    var consumeBalance: Balance?

    private var offerCount = 0

    func iceServers() async throws -> [ICEServer] {
        calls.append(.iceServers)
        if let iceServersError { throw iceServersError }
        return iceServersResult
    }

    func createSession(_ req: CreateSessionRequest) async throws -> String {
        calls.append(.createSession(callerLanguage: req.callerLanguage))
        return sessionId
    }

    func offer(_ req: OfferRequest) async throws -> OfferAnswer {
        calls.append(.offer(language: req.language))
        if let offerError { throw offerError }
        let pcId = pcIds[min(offerCount, pcIds.count - 1)]
        offerCount += 1
        return OfferAnswer(pcId: pcId, sdp: "v=0 answer", type: "answer")
    }

    func ptt(pcId: String, action: PTTAction) async throws -> PTTResult {
        calls.append(.ptt(pcId: pcId, action: action))
        let key = "\(pcId):\(action.rawValue)"
        if let error = pttErrors[key] { throw error }
        if let scripted = pttResults[key] { return scripted }
        return action == .hold ? PTTResult(flushedBytes: nil) : PTTResult(flushedBytes: 1200)
    }

    func poll(sessionId: String) async throws -> PollResponse {
        calls.append(.poll)
        if let pollError { throw pollError }
        guard !pollFrames.isEmpty else { return PollResponse(events: [], closed: false) }
        return pollFrames.removeFirst()
    }

    func hangup(pcId: String) async {
        calls.append(.hangup(pcId: pcId))
    }

    func consume(subject: String, sessionId: String, seconds: Int) async -> Balance? {
        calls.append(.consume(sessionId: sessionId, seconds: seconds))
        return consumeBalance
    }

    // Convenience views
    var pttCalls: [(pcId: String, action: PTTAction)] {
        calls.compactMap { if case let .ptt(pcId, action) = $0 { return (pcId, action) } else { return nil } }
    }
    var hangups: [String] {
        calls.compactMap { if case let .hangup(pcId) = $0 { return pcId } else { return nil } }
    }
    var consumed: [Int] {
        calls.compactMap { if case let .consume(_, seconds) = $0 { return seconds } else { return nil } }
    }
}

struct FakeError: Error, Equatable {
    let message: String
    init(_ message: String = "boom") { self.message = message }
}

// MARK: - Fake history store

@MainActor
final class FakeHistory: SessionHistoryStore {
    var saved: [SessionRecord] = []

    func save(_ s: SessionRecord) async {
        saved.append(s)
    }

    var last: SessionRecord? { saved.last }
}

// MARK: - Fake peer leg + factory

@MainActor
final class FakeLeg: PeerLeg {
    var onConnectionState: ((PeerConnectionState) -> Void)?

    /// Every value passed to `setTrackEnabled`, in order.
    var trackHistory: [Bool] = []
    var trackEnabled: Bool { trackHistory.last ?? true }
    var offerCalled = false
    var remoteAnswer: (sdp: String, type: String)?
    var closed = false
    var offerError: Error?

    /// Track state observed at the moment `createOffer` ran (§3 step 3).
    var trackEnabledDuringOffer: Bool?

    func createOffer() async throws -> LocalOffer {
        if let offerError { throw offerError }
        offerCalled = true
        trackEnabledDuringOffer = trackEnabled
        return LocalOffer(sdp: "v=0 offer")
    }

    func setRemoteAnswer(sdp: String, type: String) async throws {
        remoteAnswer = (sdp, type)
    }

    func setTrackEnabled(_ enabled: Bool) {
        trackHistory.append(enabled)
    }

    func close() {
        closed = true
        onConnectionState = nil
    }

    func simulate(_ state: PeerConnectionState) {
        onConnectionState?(state)
    }
}

@MainActor
final class FakeLegFactory: PeerLegFactory {
    var legs: [FakeLeg] = []
    var receivedIceServers: [[ICEServer]] = []
    var makeError: Error?

    func makeLeg(iceServers: [ICEServer]) throws -> PeerLeg {
        if let makeError { throw makeError }
        receivedIceServers.append(iceServers)
        let leg = FakeLeg()
        legs.append(leg)
        return leg
    }

    var legA: FakeLeg { legs[0] }
    var legB: FakeLeg { legs[1] }
}

// MARK: - Microphone permission

struct FakeMicrophone: MicrophonePermission {
    let granted: Bool
    func request() async -> Bool { granted }
}

// MARK: - Manual clock
//
// Both view models only ever see time through `SessionScheduler`; this one
// fires timers when a test says `advance(by:)`, in due order, yielding between
// fires so tasks the timers spawn get to run.

final class FakeScheduler: SessionScheduler, @unchecked Sendable {
    private final class Entry {
        let id: Int
        var due: TimeInterval
        let interval: TimeInterval?
        let block: @MainActor () -> Void
        var cancelled = false
        init(id: Int, due: TimeInterval, interval: TimeInterval?, block: @escaping @MainActor () -> Void) {
            self.id = id; self.due = due; self.interval = interval; self.block = block
        }
    }

    private final class Token: SchedulerToken {
        let entry: Entry
        init(_ entry: Entry) { self.entry = entry }
        func cancel() { entry.cancelled = true }
    }

    private(set) var now: TimeInterval = 0
    private var entries: [Entry] = []
    private var nextId = 0

    func repeating(every interval: TimeInterval, _ tick: @escaping @MainActor () -> Void) -> SchedulerToken {
        let entry = Entry(id: nextId, due: now + interval, interval: interval, block: tick)
        nextId += 1
        entries.append(entry)
        return Token(entry)
    }

    func after(_ delay: TimeInterval, _ block: @escaping @MainActor () -> Void) -> SchedulerToken {
        let entry = Entry(id: nextId, due: now + delay, interval: nil, block: block)
        nextId += 1
        entries.append(entry)
        return Token(entry)
    }

    /// Timers still armed (cancelled ones are dropped lazily).
    var pendingCount: Int { entries.filter { !$0.cancelled }.count }

    @MainActor
    func advance(by seconds: TimeInterval) async {
        let target = now + seconds
        while true {
            entries.removeAll { $0.cancelled }
            guard let next = entries.filter({ $0.due <= target + 1e-9 }).min(by: { ($0.due, $0.id) < ($1.due, $1.id) }) else { break }
            now = next.due
            next.block()
            if let interval = next.interval {
                next.due += interval
            } else {
                next.cancelled = true
            }
            // Let whatever the tick spawned run before the next tick.
            for _ in 0..<5 { await Task.yield() }
        }
        now = target
    }
}

// MARK: - Fake offline engine

@MainActor
final class FakeOfflineEngine: OfflineEngine {
    enum Call: Equatable {
        case pairStatus(String, String)
        case prepare(String, String)
        case start(locale: String)
        case finish
        case cancel
        case translate(text: String, from: String, to: String)
        case speak(text: String, locale: String)
    }

    var calls: [Call] = []

    var status: PairStatus = .installed
    var prepareResult = true
    var prepareError: Error?
    var startError: Error?
    /// Partials pushed to the caller immediately after `startRecognition`.
    var partials: [String] = []
    var finalText = ""
    var finishError: Error?
    var translations: [String: String] = [:]
    var translateError: Error?
    var speakError: Error?

    private var partialSink: (@Sendable (String) -> Void)?

    func pairStatus(_ a: String, _ b: String) async -> PairStatus {
        calls.append(.pairStatus(a, b))
        return status
    }

    func prepare(_ a: String, _ b: String) async throws -> Bool {
        calls.append(.prepare(a, b))
        if let prepareError { throw prepareError }
        return prepareResult
    }

    func startRecognition(locale: String, partial: @escaping @Sendable (String) -> Void) async throws {
        calls.append(.start(locale: locale))
        if let startError { throw startError }
        partialSink = partial
        for text in partials { partial(text) }
    }

    /// Push another partial mid-hold.
    func emitPartial(_ text: String) {
        partialSink?(text)
    }

    func finishRecognition() async throws -> String {
        calls.append(.finish)
        partialSink = nil
        if let finishError { throw finishError }
        return finalText
    }

    func cancelRecognition() async {
        calls.append(.cancel)
        partialSink = nil
    }

    func translate(_ text: String, from: String, to: String) async throws -> String {
        calls.append(.translate(text: text, from: from, to: to))
        if let translateError { throw translateError }
        return translations[text] ?? "[\(to)] \(text)"
    }

    func speak(_ text: String, locale: String) async throws {
        calls.append(.speak(text: text, locale: locale))
        if let speakError { throw speakError }
    }
}

// MARK: - Fixtures (native/fixtures/*.json, resolved relative to this file)

enum Fixtures {
    static var directory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // VocaSessionTests
            .deletingLastPathComponent() // Tests
            .deletingLastPathComponent() // VocaKit
            .deletingLastPathComponent() // ios
            .deletingLastPathComponent() // native
            .appendingPathComponent("fixtures")
    }

    static func data(_ name: String) -> Data? {
        try? Data(contentsOf: directory.appendingPathComponent(name))
    }

    struct PollEvents: Decodable {
        struct Expected: Decodable {
            let turnsSpokenByA: Int
            let turnsSpokenByB: Int
            let panelATranslatedCount: Int
            let panelBTranslatedCount: Int
            let turnFailedSides: [String]
            let closesOnFrameIndex: Int
            let ignoredEventTypes: [String]

            enum CodingKeys: String, CodingKey {
                case turnsSpokenByA = "turns_spoken_by_a"
                case turnsSpokenByB = "turns_spoken_by_b"
                case panelATranslatedCount = "panel_a_translated_count"
                case panelBTranslatedCount = "panel_b_translated_count"
                case turnFailedSides = "turn_failed_sides"
                case closesOnFrameIndex = "closes_on_frame_index"
                case ignoredEventTypes = "ignored_event_types"
            }
        }

        let sessionId: String
        let pcIdA: String
        let pcIdB: String
        let langA: String
        let langB: String
        let nameA: String
        let nameB: String
        let expected: Expected
        let frames: [PollResponse]

        enum CodingKeys: String, CodingKey {
            case sessionId = "session_id"
            case pcIdA = "pc_id_a"
            case pcIdB = "pc_id_b"
            case langA = "lang_a"
            case langB = "lang_b"
            case nameA = "name_a"
            case nameB = "name_b"
            case expected, frames
        }
    }

    static func pollEvents() -> PollEvents? {
        guard let data = data("poll_events.json") else { return nil }
        return try? JSONDecoder().decode(PollEvents.self, from: data)
    }
}
