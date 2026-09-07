import XCTest
import VocaKit
@testable import VocaSession

// The scripted `FakeOfflineEngine` lives in Fakes.swift; these tests drive it.

// MARK: - Tests

@MainActor
final class OfflineSessionViewModelTests: XCTestCase {

    private func makeModel(engine: FakeOfflineEngine,
                           langA: String = "en", langB: String = "zh",
                           scheduler: FakeScheduler = FakeScheduler()) -> OfflineSessionViewModel {
        let config = SessionConfig(langA: langA, langB: langB, nameA: "Ada", nameB: "Bo",
                                   clientId: "client-test", budgetSeconds: .infinity)
        return OfflineSessionViewModel(config: config, engine: engine, scheduler: scheduler)
    }

    // MARK: A full turn

    func testFullTurnRunsSttThenTranslateThenTts() async throws {
        let engine = FakeOfflineEngine()
        engine.finalText = "hello there"
        engine.translations = ["hello there": "你好"]
        let model = makeModel(engine: engine)

        XCTAssertEqual(model.state.badge, "On device", "§5: the centre bar shows ON DEVICE, not a live dot")
        XCTAssertEqual(model.state.phase, .live)

        model.hold(side: .a)
        await model.settle()
        XCTAssertTrue(model.state.sideA.pressing)
        XCTAssertTrue(model.state.sideB.disabled, "the other side is locked out while A holds")
        XCTAssertEqual(model.state.sideA.status, "Recording")

        model.release(side: .a)
        await model.settle()

        XCTAssertEqual(engine.calls, [
            .pairStatus("en", "zh"),
            .start(locale: "en-US"),
            .finish,
            .translate(text: "hello there", from: "en", to: "zh"),
            .speak(text: "你好", locale: "zh-CN"),
        ])
        XCTAssertNil(model.state.notice)
        XCTAssertEqual(model.phase, .idle)
        XCTAssertEqual(model.turns.count, 1)
        XCTAssertEqual(model.turns.first?.side, .a)
        XCTAssertEqual(model.turns.first?.original, "hello there")
        XCTAssertEqual(model.turns.first?.translated, "你好")
    }

    func testTurnSpokenByAIsMineOnAAndTranslatedOnB() async throws {
        let engine = FakeOfflineEngine()
        engine.finalText = "hello there"
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()
        model.release(side: .a)
        await model.settle()

        XCTAssertEqual(model.state.sideA.turns.count, 1)
        XCTAssertEqual(model.state.sideB.turns.count, 1)
        XCTAssertEqual(model.state.sideA.turns.first?.mine, true, "A's own panel: 'You said'")
        XCTAssertEqual(model.state.sideB.turns.first?.mine, false, "B's panel: 'Translated'")

        // And the same turn spoken by B lands the other way round.
        engine.finalText = "你好吗"
        engine.translations["你好吗"] = "how are you"
        model.hold(side: .b)
        await model.settle()
        model.release(side: .b)
        await model.settle()

        XCTAssertEqual(model.turns.count, 2)
        XCTAssertEqual(model.state.sideB.turns.last?.mine, true)
        XCTAssertEqual(model.state.sideA.turns.last?.mine, false)
        XCTAssertTrue(engine.calls.contains(.translate(text: "你好吗", from: "zh", to: "en")))
        XCTAssertTrue(engine.calls.contains(.speak(text: "how are you", locale: "en-US")))
    }

    // MARK: Partials

    func testPartialsAppearOnTheSpeakersOwnPanelOnly() async throws {
        let engine = FakeOfflineEngine()
        engine.finalText = "hello there"
        engine.partials = ["hel", "hello th"]
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()

        XCTAssertEqual(model.state.sideA.note, "hello th", "the running transcript belongs to the speaker")
        XCTAssertNil(model.state.sideB.note, "the listener never sees the speaker's partials")

        engine.emitPartial("hello there")
        for _ in 0..<5 { await Task.yield() }
        XCTAssertEqual(model.state.sideA.note, "hello there")

        model.release(side: .a)
        await model.settle()
        XCTAssertNil(model.state.sideA.note, "the partial is cleared once the turn is committed")
    }

    func testPartialsFromSideBAppearOnPanelB() async throws {
        let engine = FakeOfflineEngine()
        engine.partials = ["你好"]
        let model = makeModel(engine: engine)

        model.hold(side: .b)
        await model.settle()
        XCTAssertEqual(model.state.sideB.note, "你好")
        XCTAssertNil(model.state.sideA.note)
        XCTAssertEqual(engine.calls.first, .pairStatus("en", "zh"))
        XCTAssertTrue(engine.calls.contains(.start(locale: "zh-CN")))
    }

    // MARK: Unsupported / uninstalled pair

    func testUnsupportedPairRefusesTheTurnWithANotice() async throws {
        let engine = FakeOfflineEngine()
        engine.status = .unsupported
        let model = makeModel(engine: engine, langA: "en", langB: "yue")   // yue is never offline (§4)

        model.hold(side: .a)
        await model.settle()

        XCTAssertEqual(engine.calls, [.pairStatus("en", "yue")], "nothing is recorded for an unsupported pair")
        XCTAssertEqual(model.phase, .idle)
        XCTAssertFalse(model.state.sideA.pressing)
        XCTAssertFalse(model.state.sideB.disabled)
        let notice = try XCTUnwrap(model.state.notice)
        XCTAssertTrue(notice.contains("Download both translation languages"), notice)
        XCTAssertTrue(model.turns.isEmpty)
    }

    func testSupportedButNotInstalledPairAlsoRefusesTheTurn() async throws {
        // "supported" means the pack exists but has not been downloaded yet.
        let engine = FakeOfflineEngine()
        engine.status = .supported
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()

        XCTAssertEqual(engine.calls, [.pairStatus("en", "zh")])
        XCTAssertNotNil(model.state.notice)
        XCTAssertEqual(model.phase, .idle)
    }

    // MARK: Declined download

    func testDeclinedDownloadReturnsFalseAndIsNotAnError() async throws {
        let engine = FakeOfflineEngine()
        engine.status = .supported
        engine.prepareResult = false

        let installed = try await engine.prepare("en", "zh")
        XCTAssertFalse(installed, "a declined download is reported as false, never thrown")
        XCTAssertEqual(engine.calls, [.prepare("en", "zh")])
    }

    func testAcceptedDownloadReturnsTrue() async throws {
        let engine = FakeOfflineEngine()
        engine.status = .supported
        engine.prepareResult = true
        let installed = try await engine.prepare("en", "zh")
        XCTAssertTrue(installed)
    }

    // MARK: Failure paths

    func testNothingRecognisedProducesANoticeAndNoTurn() async throws {
        let engine = FakeOfflineEngine()
        engine.finalText = "   "
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()
        model.release(side: .a)
        await model.settle()

        XCTAssertTrue(model.turns.isEmpty)
        XCTAssertEqual(model.phase, .idle)
        XCTAssertNotNil(model.state.notice)
        XCTAssertFalse(engine.calls.contains(where: {
            if case .translate = $0 { return true } else { return false }
        }), "nothing to translate")
    }

    func testMissingLocalVoiceStillKeepsTheTurn() async throws {
        let engine = FakeOfflineEngine()
        engine.finalText = "hello there"
        engine.speakError = OfflineEngineError.noLocalVoice(locale: "zh-CN")
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()
        model.release(side: .a)
        await model.settle()

        XCTAssertEqual(model.turns.count, 1, "the translation is still on screen even if it cannot be spoken")
        let notice = try XCTUnwrap(model.state.notice)
        XCTAssertTrue(notice.contains("No offline voice"), notice)
        XCTAssertEqual(model.phase, .idle)
    }

    func testRecognitionPermissionDeniedIsReportedInPlainWords() async throws {
        let engine = FakeOfflineEngine()
        engine.startError = OfflineEngineError.permissionDenied
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()

        let notice = try XCTUnwrap(model.state.notice)
        XCTAssertTrue(notice.contains("permission"), notice)
        XCTAssertEqual(model.phase, .idle)
    }

    // MARK: Lockout, clock, swap, teardown

    func testTheOtherSideCannotStartAHoldWhileOneIsInFlight() async throws {
        let engine = FakeOfflineEngine()
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()
        model.hold(side: .b)
        await model.settle()

        XCTAssertTrue(model.state.sideA.pressing)
        XCTAssertFalse(model.state.sideB.pressing)
        XCTAssertEqual(engine.calls.filter { $0 == .start(locale: "zh-CN") }.count, 0)
    }

    func testClockTicksOnceASecond() async throws {
        let scheduler = FakeScheduler()
        let model = makeModel(engine: FakeOfflineEngine(), scheduler: scheduler)
        XCTAssertEqual(model.state.elapsed, 0)
        await scheduler.advance(by: 65)
        XCTAssertEqual(model.state.elapsed, 65)
        XCTAssertEqual(model.state.elapsedClock, "01:05")
    }

    func testSwapExchangesLanguagesAndClearsEarlierBubbles() async throws {
        let engine = FakeOfflineEngine()
        engine.finalText = "hello there"
        let model = makeModel(engine: engine)

        model.hold(side: .a)
        await model.settle()
        model.release(side: .a)
        await model.settle()
        XCTAssertEqual(model.turns.count, 1)

        model.swapLanguages()
        XCTAssertEqual(model.state.sideA.langCode, "zh")
        XCTAssertEqual(model.state.sideB.langCode, "en")
        XCTAssertTrue(model.turns.isEmpty, "old bubbles were authored under the old assignment")
    }

    func testEndCancelsAnInFlightRecognitionAndEndsTheSession() async throws {
        let scheduler = FakeScheduler()
        let engine = FakeOfflineEngine()
        let model = makeModel(engine: engine, scheduler: scheduler)

        model.hold(side: .a)
        await model.settle()
        model.end()
        await model.settle()

        XCTAssertEqual(model.state.phase, .ended(.user))
        XCTAssertTrue(engine.calls.contains(.cancel))
        XCTAssertEqual(scheduler.pendingCount, 0, "the clock is disarmed on teardown")

        // A late hold after End does nothing.
        let before = engine.calls.count
        model.hold(side: .b)
        await model.settle()
        XCTAssertEqual(engine.calls.count, before)
    }
}
