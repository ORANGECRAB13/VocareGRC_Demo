import Foundation

// MARK: - CONTRACT §6: `SessionHistoryStore` (VocaKit/History), consumed by VocaSession.

/// What a session hands the history store at each persist point (`active` while
/// live, `ended` at teardown). Mirrors the SwiftData schema field for field so
/// the store is a straight upsert.
public struct SessionRecord: Equatable, Sendable, Codable {
    public var sessionId: String
    public var callerName: String
    public var topic: String
    public var languageA: String
    public var languageB: String
    public var participantA: String
    public var participantB: String
    /// `active` | `ended`
    public var status: String
    public var durationSeconds: Int
    /// JSON array of transcript turns (`TranscriptTurn` shape; the poll `turn`
    /// event shape is a superset and decodes too).
    public var transcriptJSON: String
    public var startedAt: Date?

    public init(sessionId: String, callerName: String, topic: String = "Translation Session",
                languageA: String, languageB: String, participantA: String, participantB: String,
                status: String, durationSeconds: Int, transcriptJSON: String = "[]", startedAt: Date? = nil) {
        self.sessionId = sessionId
        self.callerName = callerName
        self.topic = topic
        self.languageA = languageA
        self.languageB = languageB
        self.participantA = participantA
        self.participantB = participantB
        self.status = status
        self.durationSeconds = durationSeconds
        self.transcriptJSON = transcriptJSON
        self.startedAt = startedAt
    }

    /// Convenience for callers that keep a typed transcript.
    public static func encodeTranscript<T: Encodable>(_ turns: [T]) -> String {
        let encoder = JSONEncoder()
        guard let data = try? encoder.encode(turns), let text = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return text
    }

    public var transcript: [TranscriptTurn] {
        (try? JSONDecoder().decode([TranscriptTurn].self, from: Data(transcriptJSON.utf8))) ?? []
    }

    public var isLive: Bool { status == "live" || status == "active" }
}

public protocol SessionHistoryStore: Sendable {
    func save(_ s: SessionRecord) async
}
