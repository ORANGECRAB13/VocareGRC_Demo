import Foundation

// MARK: - Injectable timers
//
// Both view models drive themselves off a 1s tick (elapsed clock, poll) plus a
// few one-shot timeouts (36s turn fallback, 5s ICE cap). Abstracting the clock
// lets the tests advance time deterministically instead of sleeping.

public protocol SchedulerToken {
    func cancel()
}

public protocol SessionScheduler {
    /// Fire `tick` every `interval` seconds on the main actor until cancelled.
    func repeating(every interval: TimeInterval, _ tick: @escaping @MainActor () -> Void) -> SchedulerToken
    /// Fire `block` once after `delay` seconds on the main actor unless cancelled.
    func after(_ delay: TimeInterval, _ block: @escaping @MainActor () -> Void) -> SchedulerToken
}

/// Wall-clock scheduler built on structured tasks.
public final class TaskScheduler: SessionScheduler {
    public init() {}

    private final class Token: SchedulerToken {
        let task: Task<Void, Never>
        init(_ task: Task<Void, Never>) { self.task = task }
        func cancel() { task.cancel() }
    }

    /// Fires on a fixed wall-clock schedule rather than sleeping a full interval
    /// after each tick.
    ///
    /// Sleeping `interval` *between* ticks makes the real period
    /// `interval + tick + main-actor scheduling delay`, and because nothing ever
    /// corrects for it the error accumulates: the transcript falls further
    /// behind the longer a session runs, and the elapsed clock — which drives
    /// metering — under-counts. `setInterval` in the webview this replaced is
    /// fixed-rate, which is why the same session felt slower here.
    ///
    /// Each wake-up is computed from the start instant, so a late tick is
    /// followed by a correspondingly shorter sleep. If the main actor was busy
    /// long enough to miss whole periods those are skipped rather than fired
    /// back to back, which is what a fixed-rate timer does and what the poll
    /// loop wants: catching up on missed polls buys nothing, the next poll
    /// drains the whole queue anyway.
    public func repeating(every interval: TimeInterval, _ tick: @escaping @MainActor () -> Void) -> SchedulerToken {
        let period = max(0.001, interval)
        return Token(Task { @MainActor in
            let start = DispatchTime.now().uptimeNanoseconds
            let periodNs = UInt64(period * 1_000_000_000)
            var iteration: UInt64 = 1
            while !Task.isCancelled {
                let deadline = start &+ (periodNs &* iteration)
                let now = DispatchTime.now().uptimeNanoseconds
                if deadline > now {
                    try? await Task.sleep(nanoseconds: deadline - now)
                } else {
                    // Ran late: skip the periods already missed and realign.
                    iteration = (now &- start) / periodNs
                    await Task.yield()
                }
                if Task.isCancelled { return }
                iteration &+= 1
                tick()
            }
        })
    }

    public func after(_ delay: TimeInterval, _ block: @escaping @MainActor () -> Void) -> SchedulerToken {
        let ns = UInt64(max(0, delay) * 1_000_000_000)
        return Token(Task { @MainActor in
            try? await Task.sleep(nanoseconds: ns)
            if Task.isCancelled { return }
            block()
        })
    }
}

/// Keeps hold of fire-and-forget work so tests (and teardown) can wait for it.
@MainActor
final class TaskTracker {
    private var tasks: [UUID: Task<Void, Never>] = [:]

    @discardableResult
    func track(_ operation: @escaping @MainActor () async -> Void) -> Task<Void, Never> {
        let id = UUID()
        let task = Task { @MainActor in
            await operation()
        }
        tasks[id] = task
        Task { @MainActor in
            _ = await task.value
            self.tasks[id] = nil
        }
        return task
    }

    /// Await every tracked task, including ones spawned while waiting.
    func settle() async {
        while let task = tasks.values.first {
            _ = await task.value
            await Task.yield()
        }
    }

    func cancelAll() {
        for task in tasks.values { task.cancel() }
        tasks.removeAll()
    }
}
