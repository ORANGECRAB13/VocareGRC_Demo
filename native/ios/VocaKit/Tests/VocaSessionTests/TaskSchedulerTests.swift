import XCTest
@testable import VocaSession

/// Timing properties of the real `TaskScheduler`.
///
/// The rest of the suite runs on `FakeScheduler`, which fires on demand and so
/// cannot observe drift at all. These are the only tests that exercise the
/// wall-clock implementation, and they exist because a drifting poll loop is
/// what made translated text arrive noticeably later in the native app than it
/// did in the webview it replaced: the audio came over WebRTC on time and the
/// transcript trailed further behind the longer a session ran.
///
/// They are deliberately tolerant. CI machines stall, so each assertion is a
/// bound generous enough to pass on a loaded runner while still failing the
/// sleep-after-tick implementation, which drifts without limit.

/// Occupies the calling thread. Simulates a main actor busy with rendering;
/// `Task.sleep` would suspend and free it, which is the opposite of what these
/// tests need. It is also unavailable directly in an async context, so the
/// blocking has to happen behind a synchronous call like this one.
private func blockMainActor(for seconds: TimeInterval) {
    Thread.sleep(forTimeInterval: seconds)
}

@MainActor
final class TaskSchedulerTests: XCTestCase {

    /// Ticks stay pinned to a fixed schedule even when the work each tick does
    /// takes a meaningful slice of the period. The old implementation slept a
    /// full period *after* every tick, so this accumulated ~50% error.
    func testFixedRateDoesNotAccumulateDriftWhenTicksAreSlow() async throws {
        let scheduler = TaskScheduler()
        let period: TimeInterval = 0.05
        let ticks = 10
        let workPerTick: TimeInterval = 0.02   // 40% of the period

        let start = Date()
        var stamps: [TimeInterval] = []
        let done = expectation(description: "ticks")

        let token = scheduler.repeating(every: period) {
            stamps.append(Date().timeIntervalSince(start))
            // Block the main actor the way a real tick's rendering would.
            blockMainActor(for: workPerTick)
            if stamps.count == ticks { done.fulfill() }
        }
        await fulfillment(of: [done], timeout: 5)
        token.cancel()

        let last = try XCTUnwrap(stamps.last)
        let ideal = period * Double(ticks)
        // Sleep-after-tick would land at ideal + ticks*workPerTick = 0.70s.
        // Fixed-rate lands near 0.50s. 0.62s separates them with room to spare.
        XCTAssertLessThan(last, ideal + Double(ticks) * workPerTick * 0.6,
                          "ticks drifted like a sleep-after-tick loop: \(stamps)")
    }

    /// A stall longer than several periods must not fire a burst of catch-up
    /// ticks. For the poll loop a missed poll is worthless — the next one
    /// drains the whole server-side queue regardless.
    func testMissedPeriodsAreSkippedRatherThanFiredBackToBack() async throws {
        let scheduler = TaskScheduler()
        let period: TimeInterval = 0.02

        var count = 0
        let firstTick = expectation(description: "first tick")
        let token = scheduler.repeating(every: period) {
            count += 1
            if count == 1 { firstTick.fulfill() }
        }
        await fulfillment(of: [firstTick], timeout: 2)

        // Stall the main actor for ~15 periods.
        blockMainActor(for: period * 15)
        let afterStall = count
        try await Task.sleep(nanoseconds: UInt64(period * 3 * 1_000_000_000))
        token.cancel()

        // A catch-up burst would add ~15 ticks the instant the actor freed up.
        XCTAssertLessThan(count - afterStall, 10,
                          "scheduler fired a catch-up burst after a stall")
    }

    /// Cancellation still stops the loop promptly — the deadline arithmetic
    /// must not leave a task sleeping past its token being cancelled.
    func testCancelStopsTheLoop() async throws {
        let scheduler = TaskScheduler()
        var count = 0
        let token = scheduler.repeating(every: 0.01) { count += 1 }
        try await Task.sleep(nanoseconds: 60_000_000)
        token.cancel()
        let atCancel = count
        try await Task.sleep(nanoseconds: 60_000_000)
        XCTAssertEqual(count, atCancel, "ticks continued after cancel")
    }
}
