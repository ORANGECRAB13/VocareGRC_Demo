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

    public func repeating(every interval: TimeInterval, _ tick: @escaping @MainActor () -> Void) -> SchedulerToken {
        let ns = UInt64(max(0.001, interval) * 1_000_000_000)
        return Token(Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: ns)
                if Task.isCancelled { return }
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
