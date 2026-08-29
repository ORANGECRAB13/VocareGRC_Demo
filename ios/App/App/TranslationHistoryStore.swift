import Foundation
import SwiftData

@Model
final class StoredTranslationSession {
    @Attribute(.unique) var sessionId: String
    var callerName: String
    var topic: String
    var languageA: String
    var languageB: String
    var participantA: String
    var participantB: String
    var status: String
    var durationSeconds: Int
    var transcriptJSON: String
    var startedAt: Date
    var updatedAt: Date

    init(sessionId: String, startedAt: Date = Date()) {
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
}

struct TranslationSessionInput {
    let sessionId: String
    let callerName: String?
    let topic: String?
    let languageA: String?
    let languageB: String?
    let participantA: String?
    let participantB: String?
    let status: String?
    let durationSeconds: Int?
    let transcriptJSON: String?
    let startedAt: Date?
}

@MainActor
final class TranslationHistoryStore {
    static let shared = TranslationHistoryStore()
    private let container: ModelContainer
    private var context: ModelContext { container.mainContext }

    private init() {
        do { container = try ModelContainer(for: StoredTranslationSession.self) }
        catch { fatalError("Unable to create translation history store: \(error)") }
    }

    func upsert(_ input: TranslationSessionInput) throws -> StoredTranslationSession {
        let sessionId = input.sessionId
        let descriptor = FetchDescriptor<StoredTranslationSession>(predicate: #Predicate { $0.sessionId == sessionId })
        let record = try context.fetch(descriptor).first ?? StoredTranslationSession(
            sessionId: sessionId, startedAt: input.startedAt ?? Date()
        )
        if record.modelContext == nil { context.insert(record) }
        record.callerName = input.callerName ?? record.callerName
        record.topic = input.topic ?? record.topic
        record.languageA = input.languageA ?? record.languageA
        record.languageB = input.languageB ?? record.languageB
        record.participantA = input.participantA ?? record.participantA
        record.participantB = input.participantB ?? record.participantB
        record.status = input.status ?? record.status
        record.durationSeconds = input.durationSeconds ?? record.durationSeconds
        record.transcriptJSON = input.transcriptJSON ?? record.transcriptJSON
        record.updatedAt = Date()
        try context.save()
        return record
    }

    func list() throws -> [StoredTranslationSession] {
        var descriptor = FetchDescriptor<StoredTranslationSession>(sortBy: [SortDescriptor(\.updatedAt, order: .reverse)])
        descriptor.fetchLimit = 250
        return try context.fetch(descriptor)
    }

    func get(_ sessionId: String) throws -> StoredTranslationSession? {
        let descriptor = FetchDescriptor<StoredTranslationSession>(predicate: #Predicate { $0.sessionId == sessionId })
        return try context.fetch(descriptor).first
    }
}
