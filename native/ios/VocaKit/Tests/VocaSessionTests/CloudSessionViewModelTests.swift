import Foundation
import VocaKit
import XCTest
@testable import VocaSession

/// CONTRACT §3 / §8 — the cloud session state machine, driven entirely through
/// fakes: no network, no WebRTC, no wall clock.
@MainActor
final class CloudSessionViewModelTests: XCTestCase {
    private var api: FakeAPI!
    private var history: FakeHistory!
    private var legs: FakeLegFactory!
    private var clock: FakeScheduler!

    override func setUp() {
        super.setUp()
        api = FakeAPI()
        history = FakeHistory()
        legs = FakeLegFactory()
        clock = FakeScheduler()
    }

    private func makeVM(budget: TimeInterval = .infinity, micGranted: Bool = true,
                        langA: String = "en", langB: String = "zh") -> CloudSessionViewModel {
        CloudSessionViewModel(
            config: SessionConfig(langA: langA, langB: langB, nameA: "Alex", nameB: "Mei",
                                  clientId: "client-1", budgetSeconds: budget),
            api: api, history: history, legFactory: legs, scheduler: clock,
            microphone: FakeMicrophone(granted: micGranted)
        )
    }

    /// Start, let setup finish, and report leg A connected.
    private func makeLiveVM(budget: TimeInterval = .infinity) async -> CloudSessionViewModel {
        let vm = makeVM(budget: budget)
        vm.start()
        await vm.settle()
        legs.legA.simulate(.connected)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .live)
        return vm
    }

    // MARK: Setup (§3 steps 1–6)

    func testSetupFollowsProtocolOrderAndTrackHandling() async {
        let vm = makeVM()
        XCTAssertEqual(vm.state.phase, .connecting)
        XCTAssertEqual(vm.state.sideA.status, "Connecting…")
        XCTAssertTrue(vm.state.sideA.disabled)
        XCTAssertEqual(vm.state.sideA.note, "Connecting…")

        vm.start()
        await vm.settle()

        XCTAssertEqual(Array(api.calls.prefix(4)), [
            .iceServers, .createSession(callerLanguage: "en"), .offer(language: "en"), .offer(language: "zh"),
        ])
        XCTAssertEqual(legs.legs.count, 2)
        XCTAssertEqual(legs.receivedIceServers.count, 2)
        // Both clones enabled during negotiation (mobile Safari rule), disabled once answered.
        XCTAssertEqual(legs.legA.trackEnabledDuringOffer, true)
        XCTAssertEqual(legs.legB.trackEnabledDuringOffer, true)
        XCTAssertEqual(legs.legA.trackEnabled, false)
        XCTAssertEqual(legs.legB.trackEnabled, false)
        XCTAssertEqual(legs.legA.remoteAnswer?.type, "answer")
        XCTAssertEqual(legs.legB.remoteAnswer?.type, "answer")
        // Persisted as active as soon as the session exists.
        XCTAssertEqual(history.saved.first?.status, "active")
        XCTAssertEqual(history.saved.first?.sessionId, "sess-1")
        XCTAssertEqual(history.saved.first?.languageA, "en")
        XCTAssertEqual(history.saved.first?.participantB, "Mei")

        // Not live until leg A connects; no timers yet.
        XCTAssertEqual(vm.state.phase, .connecting)
        XCTAssertEqual(clock.pendingCount, 0)

        legs.legA.simulate(.connected)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .live)
        XCTAssertEqual(vm.state.sideA.status, "Ready")
        XCTAssertFalse(vm.state.sideA.disabled)
        XCTAssertNil(vm.state.sideA.note)
        XCTAssertEqual(clock.pendingCount, 2, "elapsed timer + poll loop")
    }

    func testMicDeniedIsMicError() async {
        let vm = makeVM(micGranted: false)
        vm.start()
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .error(.mic))
        XCTAssertTrue(api.calls.isEmpty)
    }

    func testEmptyIceServersFailsSession() async {
        api.iceServersResult = []
        let vm = makeVM()
        vm.start()
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .error(.network))
        XCTAssertTrue(legs.legs.isEmpty)
    }

    func testOfferFailureIsNetworkError() async {
        api.offerError = FakeError("offer failed")
        let vm = makeVM()
        vm.start()
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .error(.network))
        XCTAssertTrue(legs.legA.closed)
        XCTAssertTrue(legs.legB.closed)
    }

    func testPaymentRequiredOnOfferEndsTheSessionAsExhausted() async {
        // §2: /offer answers 402 no_translation_credit. That is the paywall,
        // not a transport failure — LiveScreen maps .ended(.exhausted) to
        // onExhausted. Android: CloudSessionViewModel.kt EndReason.EXHAUSTED.
        api.offerError = APIError.paymentRequired(nil)
        let vm = makeVM()
        vm.start()
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .ended(.exhausted))
        XCTAssertNotEqual(vm.state.phase, .error(.network))
        XCTAssertTrue(legs.legs.allSatisfy(\.closed))
        // The "active" upsert from session creation may still be in flight;
        // what matters is that the transcript was persisted as ended.
        XCTAssertTrue(history.saved.contains { $0.status == "ended" })
    }

    func testConnectionLostIsNetworkError() async {
        let vm = await makeLiveVM()
        legs.legA.simulate(.failed)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .error(.network))
        XCTAssertEqual(Set(api.hangups), ["pc-a", "pc-b"])
    }

    // MARK: Push-to-talk (§3 step 7)

    func testHoldAndReleaseStateMachine() async {
        let vm = await makeLiveVM()

        vm.hold(side: .a)
        XCTAssertTrue(vm.state.sideA.pressing)
        XCTAssertEqual(vm.state.sideA.status, "Recording")
        XCTAssertEqual(vm.state.sideB.status, "Listening to Alex")
        XCTAssertTrue(vm.state.sideB.disabled)
        XCTAssertFalse(vm.state.sideB.pressing)
        // Other leg muted before the gate opens; this leg not yet unmuted.
        XCTAssertEqual(legs.legB.trackEnabled, false)
        XCTAssertEqual(legs.legA.trackEnabled, false)

        await vm.settle()
        XCTAssertEqual(api.pttCalls.map(\.pcId), ["pc-a"])
        XCTAssertEqual(api.pttCalls.map(\.action), [.hold])
        XCTAssertEqual(legs.legA.trackEnabled, true, "track only unmuted after the server gate opened")

        vm.release(side: .a)
        XCTAssertFalse(vm.state.sideA.pressing)
        XCTAssertEqual(legs.legA.trackEnabled, false)
        XCTAssertEqual(vm.state.sideA.status, "Translating...")
        XCTAssertTrue(vm.state.sideA.dimmed)
        XCTAssertTrue(vm.state.sideA.disabled)
        XCTAssertFalse(vm.state.sideB.disabled)
        XCTAssertEqual(vm.state.sideB.status, "Ready")

        await vm.settle()
        XCTAssertEqual(api.pttCalls.map(\.action), [.hold, .release])
        XCTAssertEqual(vm.state.sideA.status, "Translating...", "flushed bytes → wait for the turn event")
    }

    func testOtherSideIsLockedOutWhilePressing() async {
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()

        vm.hold(side: .b)
        await vm.settle()
        XCTAssertFalse(vm.state.sideB.pressing)
        XCTAssertTrue(vm.state.sideB.disabled)
        XCTAssertEqual(api.pttCalls.map(\.pcId), ["pc-a"], "no gate call for the locked-out side")
        XCTAssertEqual(legs.legB.trackEnabled, false)

        // Releasing B (never pressed) is a no-op.
        vm.release(side: .b)
        await vm.settle()
        XCTAssertEqual(api.pttCalls.count, 1)
    }

    func testHoldWhileTranslatingIsRefused() async {
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()
        vm.release(side: .a)
        await vm.settle()
        XCTAssertEqual(vm.state.sideA.status, "Translating...")

        vm.hold(side: .a)
        await vm.settle()
        XCTAssertFalse(vm.state.sideA.pressing)
        XCTAssertEqual(api.pttCalls.count, 2)
    }

    func testGateFailureRevertsToReady() async {
        api.pttErrors["pc-a:hold"] = FakeError("gate down")
        let vm = await makeLiveVM()

        vm.hold(side: .a)
        XCTAssertTrue(vm.state.sideA.pressing)
        await vm.settle()

        XCTAssertFalse(vm.state.sideA.pressing)
        XCTAssertEqual(vm.state.sideA.status, "Ready")
        XCTAssertFalse(vm.state.sideA.disabled)
        XCTAssertFalse(legs.legA.trackHistory.contains(true), "track never unmuted when the gate failed")
        XCTAssertFalse(vm.state.sideB.disabled)
    }

    func testReleaseFailureRevertsToReady() async {
        api.pttErrors["pc-a:release"] = FakeError("gate down")
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()
        vm.release(side: .a)
        await vm.settle()
        XCTAssertEqual(vm.state.sideA.status, "Ready")
        XCTAssertFalse(vm.state.sideA.dimmed)
    }

    func testGateCallsAreSerialisedPerSide() async {
        let vm = await makeLiveVM()
        // Hold and release before the hold round-trip settles.
        vm.hold(side: .a)
        vm.release(side: .a)
        await vm.settle()
        XCTAssertEqual(api.pttCalls.map(\.action), [.hold, .release])
        XCTAssertEqual(api.pttCalls.map(\.pcId), ["pc-a", "pc-a"])
        XCTAssertEqual(legs.legA.trackEnabled, false, "a press that ended before the gate opened never unmutes")
    }

    // MARK: flushed_bytes short-circuit + 36s fallback

    func testReleaseWithoutFlushedBytesReturnsToReadyImmediately() async {
        api.pttResults["pc-a:release"] = PTTResult(flushedBytes: nil)
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()
        vm.release(side: .a)
        XCTAssertEqual(vm.state.sideA.status, "Translating...")
        await vm.settle()
        XCTAssertEqual(vm.state.sideA.status, "Ready")
        XCTAssertFalse(vm.state.sideA.dimmed)
        XCTAssertFalse(vm.state.sideA.disabled)
    }

    func testReleaseWithZeroFlushedBytesAlsoShortCircuits() async {
        api.pttResults["pc-a:release"] = PTTResult(flushedBytes: 0)
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()
        vm.release(side: .a)
        await vm.settle()
        XCTAssertEqual(vm.state.sideA.status, "Ready")
    }

    func testTranslatingFallsBackToReadyAfter36Seconds() async {
        let vm = await makeLiveVM()
        vm.hold(side: .b)
        await vm.settle()
        vm.release(side: .b)
        await vm.settle()
        XCTAssertEqual(vm.state.sideB.status, "Translating...")

        await clock.advance(by: 35)
        XCTAssertEqual(vm.state.sideB.status, "Translating...")
        XCTAssertTrue(vm.state.sideB.disabled)

        await clock.advance(by: 1)
        XCTAssertEqual(vm.state.sideB.status, "Ready")
        XCTAssertFalse(vm.state.sideB.disabled)
        XCTAssertFalse(vm.state.sideB.dimmed)
    }

    func testTurnEventCancelsFallbackTimer() async {
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()
        vm.release(side: .a)
        await vm.settle()

        api.pollFrames = [PollResponse(events: [
            PollEvent(type: "turn", speaker: "pc-a", originalLang: "en", original: "Hi", translated: "你好"),
        ], closed: false)]
        await clock.advance(by: 1)
        await vm.settle()
        XCTAssertEqual(vm.state.sideA.status, "Ready - speak again")

        // The 36s fallback must not fire later and disturb a fresh press.
        vm.hold(side: .a)
        await vm.settle()
        await clock.advance(by: 40)
        XCTAssertTrue(vm.state.sideA.pressing, "stale fallback did not reset an active press")
    }

    // MARK: Poll + turn attribution (§3 step 8)

    func testTurnSpokenByAAppearsTranslatedOnBAndYouSaidOnA() async {
        let vm = await makeLiveVM()
        vm.hold(side: .a)
        await vm.settle()
        vm.release(side: .a)
        await vm.settle()

        api.pollFrames = [PollResponse(events: [
            PollEvent(type: "turn", speaker: "pc-a", originalLang: "en", original: "Hello", translated: "你好", speakerName: "Alex"),
        ], closed: false)]
        await clock.advance(by: 1)
        await vm.settle()

        XCTAssertEqual(vm.state.sideA.turns.count, 1)
        XCTAssertEqual(vm.state.sideB.turns.count, 1)
        XCTAssertTrue(vm.state.sideA.turns[0].mine, "A's own words are 'You said' on A")
        XCTAssertFalse(vm.state.sideB.turns[0].mine, "…and 'Translated' on B")
        XCTAssertEqual(vm.state.sideB.turns[0].translated, "你好")
        XCTAssertEqual(vm.state.sideB.turns[0].original, "Hello")
        XCTAssertEqual(vm.state.sideA.status, "Ready - speak again")
        XCTAssertEqual(vm.state.sideB.status, "Ready")
        // Transcript persisted with snake_case keys for the history screen.
        let record = history.saved.last
        XCTAssertEqual(record?.status, "active")
        XCTAssertTrue(record?.transcriptJSON.contains("\"original_lang\":\"en\"") == true)
        XCTAssertTrue(record?.transcriptJSON.contains("\"speaker\":\"pc-a\"") == true)
        XCTAssertEqual(record?.transcript.count, 1)
    }

    func testTurnWithoutSpeakerFallsBackToOriginalLanguage() async {
        let vm = await makeLiveVM()
        api.pollFrames = [PollResponse(events: [
            PollEvent(type: "turn", speaker: nil, originalLang: "zh", original: "你好", translated: "Hello"),
        ], closed: false)]
        await clock.advance(by: 1)
        await vm.settle()
        XCTAssertTrue(vm.state.sideB.turns[0].mine, "zh is B's language → B spoke it")
        XCTAssertFalse(vm.state.sideA.turns[0].mine)
    }

    func testTurnFailedReleasesThatSide() async {
        let vm = await makeLiveVM()
        vm.hold(side: .b)
        await vm.settle()
        vm.release(side: .b)
        await vm.settle()
        XCTAssertEqual(vm.state.sideB.status, "Translating...")

        api.pollFrames = [PollResponse(events: [PollEvent(type: "turn_failed", speaker: "pc-b")], closed: false)]
        await clock.advance(by: 1)
        await vm.settle()
        XCTAssertEqual(vm.state.sideB.status, "Ready")
        XCTAssertTrue(vm.state.sideB.turns.isEmpty)
    }

    func testUnknownEventTypesAreIgnored() async {
        let vm = await makeLiveVM()
        api.pollFrames = [PollResponse(events: [
            PollEvent(type: "status"), PollEvent(type: "live", speaker: "pc-a", original: "Hel"),
        ], closed: false)]
        await clock.advance(by: 1)
        await vm.settle()
        XCTAssertTrue(vm.state.sideA.turns.isEmpty)
        XCTAssertEqual(history.saved.count, 1, "no extra persist for non-turn events")
    }

    func testPollErrorsAreSwallowed() async {
        let vm = await makeLiveVM()
        api.pollError = FakeError("502")
        await clock.advance(by: 3)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .live)
        XCTAssertEqual(api.calls.filter { $0 == .poll }.count, 3)
    }

    func testClosedTrueEndsSession() async {
        let vm = await makeLiveVM()
        api.pollFrames = [PollResponse(events: [], closed: true)]
        await clock.advance(by: 1)
        await vm.settle()

        XCTAssertEqual(vm.state.phase, .ended(.serverClosed))
        XCTAssertEqual(history.saved.last?.status, "ended")
        XCTAssertEqual(Set(api.hangups), ["pc-a", "pc-b"])
        XCTAssertTrue(legs.legs.allSatisfy(\.closed))
        XCTAssertEqual(clock.pendingCount, 0, "all timers cancelled")
    }

    func testFixturePollFramesReachExpectedCounts() async throws {
        let fixture = try XCTUnwrap(Fixtures.pollEvents(), "native/fixtures/poll_events.json missing")
        api.sessionId = fixture.sessionId
        api.pcIds = [fixture.pcIdA, fixture.pcIdB]
        let vm = makeVM(langA: fixture.langA, langB: fixture.langB)
        vm.start()
        await vm.settle()
        legs.legA.simulate(.connected)
        await vm.settle()

        api.pollFrames = fixture.frames
        var closedAt: Int?
        for index in fixture.frames.indices {
            // Put B into "translating" right before its turn_failed so the
            // release is observable.
            if fixture.frames[index].events.contains(where: { $0.type == "turn_failed" }) {
                vm.hold(side: .b); await vm.settle()
                vm.release(side: .b); await vm.settle()
                XCTAssertEqual(vm.state.sideB.status, "Translating...")
            }
            await clock.advance(by: 1)
            await vm.settle()
            if vm.state.phase.isEnded, closedAt == nil { closedAt = index }
            if fixture.frames[index].events.contains(where: { $0.type == "turn_failed" }) {
                XCTAssertFalse(vm.state.sideB.disabled, "turn_failed released side B")
            }
        }

        let expected = fixture.expected
        XCTAssertEqual(closedAt, expected.closesOnFrameIndex)
        XCTAssertEqual(vm.state.phase, .ended(.serverClosed))
        XCTAssertEqual(vm.state.sideA.turns.filter { !$0.mine }.count, expected.panelATranslatedCount)
        XCTAssertEqual(vm.state.sideB.turns.filter { !$0.mine }.count, expected.panelBTranslatedCount)
        XCTAssertEqual(vm.state.sideA.turns.filter(\.mine).count, expected.turnsSpokenByA)
        XCTAssertEqual(vm.state.sideB.turns.filter(\.mine).count, expected.turnsSpokenByB)
        XCTAssertEqual(history.saved.last?.transcript.count, expected.turnsSpokenByA + expected.turnsSpokenByB)
        XCTAssertEqual(Set(api.hangups), [fixture.pcIdA, fixture.pcIdB])
    }

    // MARK: Metering (§3 step 9)

    func testConsumeReportsTotalSecondsEvery15Seconds() async {
        let vm = await makeLiveVM()
        await clock.advance(by: 14)
        await vm.settle()
        XCTAssertEqual(api.consumed, [])

        await clock.advance(by: 1)
        await vm.settle()
        XCTAssertEqual(api.consumed, [15])
        XCTAssertEqual(vm.state.elapsed, 15)
        XCTAssertEqual(vm.state.elapsedClock, "00:15")

        await clock.advance(by: 15)
        await vm.settle()
        XCTAssertEqual(api.consumed, [15, 30], "totals, never deltas")

        await clock.advance(by: 2)
        vm.end()
        await vm.settle()
        XCTAssertEqual(api.consumed, [15, 30, 32], "End reports the final total")
        XCTAssertEqual(history.saved.last?.durationSeconds, 32)
        if case let .consume(sessionId, _) = api.calls.last(where: { if case .consume = $0 { return true } else { return false } })! {
            XCTAssertEqual(sessionId, "sess-1")
        }
    }

    func testConsumeWatermarkIsIdempotentAcrossRepeatedReports() async {
        // Reporting the same total twice (e.g. tick + End at the same second)
        // must send the same number, so the server settles to one balance.
        let vm = await makeLiveVM()
        await clock.advance(by: 15)
        vm.end()
        await vm.settle()
        XCTAssertEqual(api.consumed, [15, 15])
    }

    func testBudgetExhaustionEndsSession() async {
        let vm = await makeLiveVM(budget: 20)
        await clock.advance(by: 19)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .live)

        await clock.advance(by: 1)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .ended(.exhausted))
        XCTAssertEqual(api.consumed.last, 20)
        XCTAssertEqual(history.saved.last?.status, "ended")
        XCTAssertEqual(history.saved.last?.durationSeconds, 20)
        XCTAssertEqual(Set(api.hangups), ["pc-a", "pc-b"])
        XCTAssertTrue(legs.legs.allSatisfy(\.closed))
        XCTAssertEqual(clock.pendingCount, 0)
    }

    func testUnmeteredBudgetNeverEnds() async {
        let vm = await makeLiveVM(budget: .infinity)
        await clock.advance(by: 600)
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .live)
        XCTAssertEqual(vm.state.elapsedClock, "10:00")
    }

    // MARK: Teardown (§3 step 10)

    func testEndHangsUpBothLegsAndPersists() async {
        let vm = await makeLiveVM()
        api.pollFrames = [PollResponse(events: [
            PollEvent(type: "turn", speaker: "pc-b", originalLang: "zh", original: "你好", translated: "Hello"),
        ], closed: false)]
        await clock.advance(by: 3)
        await vm.settle()

        vm.end()
        await vm.settle()

        XCTAssertEqual(vm.state.phase, .ended(.user))
        XCTAssertEqual(Set(api.hangups), ["pc-a", "pc-b"])
        XCTAssertEqual(api.hangups.count, 2)
        XCTAssertTrue(legs.legA.closed)
        XCTAssertTrue(legs.legB.closed)
        XCTAssertEqual(clock.pendingCount, 0)
        let record = history.saved.last
        XCTAssertEqual(record?.status, "ended")
        XCTAssertEqual(record?.durationSeconds, 3)
        XCTAssertEqual(record?.transcript.first?.translated, "Hello")
        // Nothing runs after teardown.
        let pollsBefore = api.calls.filter { $0 == .poll }.count
        await clock.advance(by: 5)
        await vm.settle()
        XCTAssertEqual(api.calls.filter { $0 == .poll }.count, pollsBefore)
        vm.hold(side: .a)
        XCTAssertFalse(vm.state.sideA.pressing)
    }

    func testEndIsIdempotent() async {
        let vm = await makeLiveVM()
        vm.end()
        vm.end()
        await vm.settle()
        XCTAssertEqual(api.hangups.count, 2)
        XCTAssertEqual(history.saved.filter { $0.status == "ended" }.count, 1)
    }

    func testEndBeforeConnectedStillHangsUpKnownLegs() async {
        let vm = makeVM()
        vm.start()
        await vm.settle()
        vm.end()
        await vm.settle()
        XCTAssertEqual(vm.state.phase, .ended(.user))
        XCTAssertEqual(Set(api.hangups), ["pc-a", "pc-b"])
        XCTAssertTrue(legs.legs.allSatisfy(\.closed))
    }

    // MARK: Models

    func testClockFormatting() {
        XCTAssertEqual(LiveState.formatClock(0), "00:00")
        XCTAssertEqual(LiveState.formatClock(65), "01:05")
        XCTAssertEqual(LiveState.formatClock(-4), "00:00")
        XCTAssertEqual(LiveState.formatClock(3600), "60:00")
    }

    func testTranscriptEncodingUsesPollEventKeys() throws {
        let json = SessionTurn.encodeTranscript([
            SessionTurn(speaker: "pc-a", speakerName: "Alex", originalLang: "en", original: "Hi", translated: "你好"),
        ])
        let decoded = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [[String: Any]])
        XCTAssertEqual(decoded.first?["original_lang"] as? String, "en")
        XCTAssertEqual(decoded.first?["speaker_name"] as? String, "Alex")
        XCTAssertEqual(decoded.first?["translated"] as? String, "你好")
    }
}
