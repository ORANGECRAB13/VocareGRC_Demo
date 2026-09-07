import Foundation
import SwiftData

// MARK: - SwiftData schema (ported from ios/App/App/TranslationHistoryStore.swift)

@Model
public final class StoredTranslationSession {
    @Attribute(.unique) public var sessionId: String
    public var callerName: String
    public var topic: String
    public var languageA: String
    public var languageB: String
    public var participantA: String
    public var participantB: String
    public var status: String
    public var durationSeconds: Int
    public var transcriptJSON: String
    public var startedAt: Date
    public var updatedAt: Date

    public init(sessionId: String, startedAt: Date = Date()) {
        self.sessionId = sessionId
        callerName = "Person A"
        topic = "Translation Session"
        languageA = "en"
        languageB = "zh"
        participantA = "Person A"
        participantB = "Person B"
        status = "active"
        durationSeconds = 0
        transcriptJSON = "[]"
        self.startedAt = startedAt
        updatedAt = startedAt
    }

    public var summary: SessionSummary {
        SessionSummary(sessionId: sessionId, callerName: callerName, topic: topic, lang: languageA,
                       status: status, duration: durationSeconds, participantCount: 2)
    }

    public var detail: SessionDetail {
        let transcript = (try? JSONDecoder().decode([TranscriptTurn].self, from: Data(transcriptJSON.utf8))) ?? []
        return SessionDetail(sessionId: sessionId, callerName: callerName, lang: languageA, topic: topic,
                             status: status, transcript: transcript, participantA: participantA,
                             participantB: participantB, duration: durationSeconds)
    }
}

/// The durable local history. Conforms to `SessionHistoryStore` so both session
/// view models persist through it; `save` is an upsert keyed by `sessionId`.
@MainActor
public final class HistoryStore: SessionHistoryStore {
    private let container: ModelContainer
    private var context: ModelContext { container.mainContext }

    public init(container: ModelContainer) {
        self.container = container
    }

    /// The on-disk store the app uses.
    public static func persistent() throws -> HistoryStore {
        HistoryStore(container: try ModelContainer(for: StoredTranslationSession.self))
    }

    /// Throwaway store for tests and previews.
    public static func inMemory() throws -> HistoryStore {
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        return HistoryStore(container: try ModelContainer(for: StoredTranslationSession.self, configurations: config))
    }

    public func save(_ s: SessionRecord) async {
        _ = try? upsert(s)
    }

    @discardableResult
    public func upsert(_ input: SessionRecord) throws -> StoredTranslationSession {
        let sessionId = input.sessionId
        let descriptor = FetchDescriptor<StoredTranslationSession>(predicate: #Predicate { $0.sessionId == sessionId })
        let record = try context.fetch(descriptor).first
            ?? StoredTranslationSession(sessionId: sessionId, startedAt: input.startedAt ?? Date())
        if record.modelContext == nil { context.insert(record) }
        record.callerName = input.callerName
        record.topic = input.topic
        record.languageA = input.languageA
        record.languageB = input.languageB
        record.participantA = input.participantA
        record.participantB = input.participantB
        record.status = input.status
        record.durationSeconds = input.durationSeconds
        record.transcriptJSON = input.transcriptJSON
        record.updatedAt = Date()
        try context.save()
        return record
    }

    public func list(limit: Int = 250) throws -> [StoredTranslationSession] {
        var descriptor = FetchDescriptor<StoredTranslationSession>(sortBy: [SortDescriptor(\.updatedAt, order: .reverse)])
        descriptor.fetchLimit = limit
        return try context.fetch(descriptor)
    }

    public func get(_ sessionId: String) throws -> StoredTranslationSession? {
        let descriptor = FetchDescriptor<StoredTranslationSession>(predicate: #Predicate { $0.sessionId == sessionId })
        return try context.fetch(descriptor).first
    }
}

/// JSX `loadSessionHistory` / `loadSessionDetail`: the local store is the
/// truth, with any server-side session that is still live merged in on top.
@MainActor
public struct HistoryRepository {
    private let store: HistoryStore
    private let api: VocaHistoryAPI?
    private let clientId: String

    public init(store: HistoryStore, api: VocaHistoryAPI?, clientId: String) {
        self.store = store
        self.api = api
        self.clientId = clientId
    }

    public func list() async -> [SessionSummary] {
        let local = ((try? store.list()) ?? []).map(\.summary)
        guard let api else { return local }
        let localIds = Set(local.map(\.sessionId))
        let remoteLive = ((try? await api.sessions(clientId: clientId)) ?? [])
            .filter { $0.isLive && !localIds.contains($0.sessionId) }
        return remoteLive + local
    }

    public func detail(_ sessionId: String) async -> SessionDetail? {
        if let local = try? store.get(sessionId) { return local.detail }
        return try? await api?.session(id: sessionId)
    }
}
