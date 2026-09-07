import Foundation
import VocaKit

// MARK: - Cloud session (CONTRACT §3, mirrors JSX `FaceToFaceLiveScreen`)
//
// Two WebRTC legs against the backend, push-to-talk gated server side, a 1s
// poll for turn events, a 15s metering watermark and a budget cut-off. Every
// timer goes through `SessionScheduler` and every side effect through the
// injected protocols, so the whole state machine runs under XCTest with fakes.

@MainActor
public final class CloudSessionViewModel: ObservableObject {
    @Published public private(set) var state: LiveState

    public static let turnFallbackSeconds: TimeInterval = 36
    public static let usageReportEvery = 15
    public static let pollInterval: TimeInterval = 1
    public static let topic = "Translation Session"

    // Dependencies
    private let config: SessionConfig
    private let api: VocaAPI
    private let history: SessionHistoryStore
    private let legFactory: PeerLegFactory
    private let scheduler: SessionScheduler
    private let microphone: MicrophonePermission

    // Session
    private var sessionId: String?
    private var legs: [Side: PeerLeg] = [:]
    private var pcIds: [Side: String] = [:]
    /// Leg A's pc_id, kept past teardown: the transcript is re-projected after
    /// the session ends and turn attribution still has to work.
    private var speakerPcIdA: String?
    private var transcript: [SessionTurn] = []
    private var elapsedSeconds = 0
    private var connectionNote = "Connecting…"
    private var phase: LivePhase = .connecting
    private var tornDown = false
    /// `end()` is called from a button; two taps must not persist twice.
    private var ending = false

    // Push-to-talk
    private var pressing: [Side: Bool] = [.a: false, .b: false]
    private var turnState: [Side: TurnState] = [.a: .ready, .b: .ready]
    private var pressToken: [Side: Int] = [.a: 0, .b: 0]
    private var gateQueue: [Side: Task<PTTResult, Error>] = [:]
    private var turnTimeouts: [Side: SchedulerToken] = [:]

    // Timers
    private var timer: SchedulerToken?
    private var poller: SchedulerToken?
    private var pollInFlight = false
    private let tasks = TaskTracker()

    public init(config: SessionConfig, api: VocaAPI, history: SessionHistoryStore,
                legFactory: PeerLegFactory? = nil, scheduler: SessionScheduler = TaskScheduler(),
                microphone: MicrophonePermission = AVMicrophonePermission()) {
        self.config = config
        self.api = api
        self.history = history
        self.legFactory = legFactory ?? WebRTCLegFactory()
        self.scheduler = scheduler
        self.microphone = microphone
        self.state = LiveState(
            sideA: SideState(name: config.nameA, langCode: config.langA, langLabel: SessionLanguages.label(config.langA)),
            sideB: SideState(name: config.nameB, langCode: config.langB, langLabel: SessionLanguages.label(config.langB))
        )
        publish()
    }

    // MARK: Lifecycle

    /// §3 steps 1–5. Fire-and-forget; progress is reported through `state`.
    public func start() {
        tasks.track { [weak self] in await self?.setupSession() }
    }

    /// End pressed: persist, report usage, hang up, done.
    public func end() {
        guard !tornDown, !ending else { return }
        ending = true
        tasks.track { [weak self] in
            guard let self else { return }
            await self.persistSession(status: "ended")
            await self.reportElapsed()
            self.teardown()
            self.phase = .ended(.user)
            self.publish()
        }
    }

    /// Await every in-flight task (setup, gates, polls). Tests use this so
    /// assertions run after the fire-and-forget work has settled.
    public func settle() async {
        await tasks.settle()
    }

    private func setupSession() async {
        do {
            // 1. Mic
            guard await microphone.request() else {
                fail(.mic)
                return
            }
            if tornDown { return }

            // 2. ICE servers — an empty list fails the session.
            let iceServers = try await api.iceServers()
            guard !iceServers.isEmpty else { throw CloudSessionError.noIceServers }
            if tornDown { return }

            // 3. Session
            let sessionId = try await api.createSession(CreateSessionRequest(
                callerName: config.nameA, callerLanguage: config.langA,
                topic: CloudSessionViewModel.topic, clientId: config.clientId
            ))
            if tornDown { return }
            self.sessionId = sessionId
            tasks.track { [weak self] in await self?.persistSession(status: "active") }

            // 4. Two relay-only peer connections, mic cloned per leg (enabled
            //    for negotiation — see §3 step 3).
            let legA = try legFactory.makeLeg(iceServers: iceServers)
            let legB = try legFactory.makeLeg(iceServers: iceServers)
            legs[.a] = legA
            legs[.b] = legB

            // 5. Connection state: leg A is the one that starts the clocks.
            legA.onConnectionState = { [weak self] connectionState in
                self?.handleConnectionState(connectionState)
            }
            legB.onConnectionState = { [weak self] connectionState in
                guard let self, connectionState == .failed || connectionState == .disconnected else { return }
                self.handleConnectionState(connectionState)
            }

            // 6. Offers, one per leg, then exchange with the server in order.
            let offerA = try await legA.createOffer()
            let offerB = try await legB.createOffer()
            if tornDown { return }

            let answerA = try await api.offer(OfferRequest(
                sessionId: sessionId, language: config.langA, name: config.nameA, sdp: offerA.sdp, type: offerA.type
            ))
            if tornDown { return }
            pcIds[.a] = answerA.pcId
            speakerPcIdA = answerA.pcId
            try await legA.setRemoteAnswer(sdp: answerA.sdp, type: answerA.type)
            if tornDown { return }

            let answerB = try await api.offer(OfferRequest(
                sessionId: sessionId, language: config.langB, name: config.nameB, sdp: offerB.sdp, type: offerB.type
            ))
            if tornDown { return }
            pcIds[.b] = answerB.pcId
            try await legB.setRemoteAnswer(sdp: answerB.sdp, type: answerB.type)
            if tornDown { return }

            // RTP is established; push-to-talk owns the tracks from here on.
            legA.setTrackEnabled(false)
            legB.setTrackEnabled(false)
        } catch {
            if tornDown { return }
            fail(.network)
        }
    }

    private func handleConnectionState(_ connectionState: PeerConnectionState) {
        guard !tornDown else { return }
        switch connectionState {
        case .connected:
            guard phase == .connecting else { return }
            phase = .live
            connectionNote = "Connected"
            startTimer()
            startPoll()
            publish()
        case .failed, .disconnected:
            connectionNote = "Connection lost"
            fail(.network)
        default:
            break
        }
    }

    private func fail(_ error: SessionError) {
        guard !tornDown else { return }
        if error == .network, connectionNote == "Connecting…" { connectionNote = "Connection failed" }
        teardown()
        phase = .error(error)
        publish()
    }

    // MARK: Timer + metering (§3 step 9)

    private func startTimer() {
        guard timer == nil else { return }
        timer = scheduler.repeating(every: 1) { [weak self] in self?.tick() }
    }

    private func tick() {
        guard !tornDown else { return }
        elapsedSeconds += 1
        let spent = elapsedSeconds

        if spent % CloudSessionViewModel.usageReportEvery == 0 {
            tasks.track { [weak self] in await self?.reportElapsed() }
        }

        // Out of credit: end the call rather than let it run on unbilled. The
        // transcript is persisted first so the user keeps what was said.
        if TimeInterval(spent) >= config.budgetSeconds {
            tasks.track { [weak self] in
                guard let self else { return }
                await self.reportElapsed()
                await self.persistSession(status: "ended")
                self.teardown()
                self.phase = .ended(.exhausted)
                self.publish()
            }
            return
        }
        publish()
    }

    private func reportElapsed() async {
        guard let sessionId else { return }
        _ = await api.consume(subject: config.clientId, sessionId: sessionId, seconds: elapsedSeconds)
    }

    // MARK: Poll (§3 step 8)

    private func startPoll() {
        guard poller == nil else { return }
        poller = scheduler.repeating(every: CloudSessionViewModel.pollInterval) { [weak self] in
            self?.tasks.track { [weak self] in await self?.pollOnce() }
        }
    }

    private func pollOnce() async {
        guard !tornDown, !pollInFlight, let sessionId else { return }
        pollInFlight = true
        defer { pollInFlight = false }

        let response: PollResponse
        do {
            response = try await api.poll(sessionId: sessionId)
        } catch {
            return
        }
        guard !tornDown else { return }

        var sawTurn = false
        for event in response.events {
            switch event.type {
            case "turn":
                sawTurn = true
                let turn = SessionTurn(
                    speaker: event.speaker, speakerName: event.speakerName,
                    originalLang: event.originalLang ?? "", original: event.original ?? "",
                    translated: event.translated ?? ""
                )
                transcript.append(turn)
                markTurnReady(spokenBy(turn))
            case "turn_failed":
                // The server gave up on this turn; release the speaker now
                // instead of waiting for the 36s client fallback.
                markTurnReady(event.speaker == speakerPcIdA ? .a : .b)
            default:
                break // status and unknown types are ignored
            }
        }
        if sawTurn {
            tasks.track { [weak self] in await self?.persistSession(status: "active") }
        }
        publish()

        if response.closed {
            await persistSession(status: "ended")
            teardown()
            phase = .ended(.serverClosed)
            publish()
        }
    }

    /// Attribute by `speaker == pc_id`; fall back to the original language.
    private func spokenBy(_ turn: SessionTurn) -> Side {
        if let speaker = turn.speaker, !speaker.isEmpty {
            return speaker == speakerPcIdA ? .a : .b
        }
        return turn.originalLang == config.langA ? .a : .b
    }

    // MARK: Push-to-talk (§3 step 7)

    public func hold(side: Side) {
        guard phase == .live, turnState[side] == .ready, pressing[side.other] == false else { return }
        let token = (pressToken[side] ?? 0) + 1
        pressToken[side] = token

        legs[side.other]?.setTrackEnabled(false)
        pressing[side.other] = false
        pressing[side] = true
        turnState[side] = .recording
        publish()

        let gate = queueGate(side, .hold)
        tasks.track { [weak self] in
            guard let self else { return }
            do {
                _ = try await gate.value
                // Only unmute if this press is still the live one.
                if self.pressToken[side] == token, self.pressing[side] == true {
                    self.legs[side]?.setTrackEnabled(true)
                }
            } catch {
                if self.pressToken[side] == token, self.pressing[side] == true {
                    self.pressing[side] = false
                    self.turnState[side] = .ready
                    self.publish()
                }
            }
        }
    }

    public func release(side: Side) {
        guard pressing[side] == true else { return }
        legs[side]?.setTrackEnabled(false)
        pressing[side] = false
        markTurnTranslating(side)
        publish()

        let gate = queueGate(side, .release)
        tasks.track { [weak self] in
            guard let self else { return }
            do {
                let result = try await gate.value
                // Nothing captured → nothing to wait for.
                if (result.flushedBytes ?? 0) == 0 { self.markTurnReady(side) }
            } catch {
                self.markTurnReady(side)
            }
            self.publish()
        }
    }

    /// Per-side FIFO so a quick hold/release never reaches the server out of order.
    private func queueGate(_ side: Side, _ action: PTTAction) -> Task<PTTResult, Error> {
        let previous = gateQueue[side]
        let pcId = pcIds[side]
        let api = self.api
        let task = Task<PTTResult, Error> { @MainActor in
            _ = await previous?.result
            guard let pcId else { throw CloudSessionError.notConnected }
            return try await api.ptt(pcId: pcId, action: action)
        }
        gateQueue[side] = task
        return task
    }

    private func markTurnTranslating(_ side: Side) {
        clearTurnTimeout(side)
        turnState[side] = .translating
        // If an upstream translation fails without producing a turn event,
        // don't strand this speaker forever (same window as the backend).
        turnTimeouts[side] = scheduler.after(CloudSessionViewModel.turnFallbackSeconds) { [weak self] in
            self?.markTurnReady(side)
            self?.publish()
        }
    }

    private func markTurnReady(_ side: Side) {
        clearTurnTimeout(side)
        guard !tornDown else { return }
        turnState[side] = .ready
    }

    private func clearTurnTimeout(_ side: Side) {
        turnTimeouts[side]?.cancel()
        turnTimeouts[side] = nil
    }

    // MARK: Persistence + teardown (§3 step 10)

    private func persistSession(status: String) async {
        guard let sessionId else { return }
        let record = SessionRecord(
            sessionId: sessionId,
            callerName: config.nameA,
            topic: CloudSessionViewModel.topic,
            languageA: config.langA,
            languageB: config.langB,
            participantA: config.nameA,
            participantB: config.nameB,
            status: status,
            durationSeconds: elapsedSeconds,
            transcriptJSON: SessionTurn.encodeTranscript(transcript)
        )
        await history.save(record)
    }

    private func teardown() {
        guard !tornDown else { return }
        tornDown = true
        timer?.cancel(); timer = nil
        poller?.cancel(); poller = nil
        clearTurnTimeout(.a)
        clearTurnTimeout(.b)

        for side in Side.allCases {
            if let pcId = pcIds[side] {
                let api = self.api
                tasks.track { await api.hangup(pcId: pcId) }
            }
            legs[side]?.close()
        }
        pcIds.removeAll()
        legs.removeAll()

        pressing = [.a: false, .b: false]
        turnState = [.a: .ready, .b: .ready]
    }

    // MARK: Projection → LiveState (JSX render block)

    private func publish() {
        let live = phase == .live
        let spokenA = transcript.map { spokenBy($0) == .a }
        let hasSpokenA = spokenA.contains(true)
        let hasSpokenB = spokenA.contains(false)

        func side(_ side: Side, name: String, otherName: String, hasSpoken: Bool) -> SideState {
            let pressingThis = pressing[side] == true
            let pressingOther = pressing[side.other] == true
            let translating = turnState[side] == .translating
            let status: String
            if pressingThis { status = "Recording" }
            else if translating { status = "Translating..." }
            else if pressingOther { status = "Listening to \(otherName)" }
            else if live { status = hasSpoken ? "Ready - speak again" : "Ready" }
            else { status = connectionNote }

            let turns = transcript.enumerated().map { index, turn in
                TurnView(id: index, original: turn.original, translated: turn.translated,
                         mine: spokenA[index] == (side == .a))
            }
            let code = side == .a ? config.langA : config.langB
            return SideState(
                name: name, langCode: code, langLabel: SessionLanguages.label(code),
                pressing: pressingThis,
                disabled: !live || translating || pressingOther,
                dimmed: translating,
                status: status,
                turns: turns,
                note: (phase == .connecting && transcript.isEmpty) ? connectionNote : nil
            )
        }

        state = LiveState(
            sideA: side(.a, name: config.nameA, otherName: config.nameB, hasSpoken: hasSpokenA),
            sideB: side(.b, name: config.nameB, otherName: config.nameA, hasSpoken: hasSpokenB),
            elapsed: TimeInterval(elapsedSeconds),
            phase: phase,
            badge: nil,
            notice: nil
        )
    }
}

// MARK: - Transcript JSON (matches the JSX event shape the history store already reads)

extension SessionTurn {
    private struct Wire: Codable {
        var speaker: String?
        var speaker_name: String?
        var original_lang: String
        var target_lang: String?
        var original: String
        var translated: String
        var side: String?
    }

    public static func encodeTranscript(_ turns: [SessionTurn], sides: [Side]? = nil) -> String {
        let wire = turns.enumerated().map { index, turn in
            Wire(speaker: turn.speaker, speaker_name: turn.speakerName, original_lang: turn.originalLang,
                 target_lang: turn.targetLang, original: turn.original, translated: turn.translated,
                 side: sides.flatMap { index < $0.count ? $0[index].rawValue : nil })
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(wire), let text = String(data: data, encoding: .utf8) else { return "[]" }
        return text
    }
}
