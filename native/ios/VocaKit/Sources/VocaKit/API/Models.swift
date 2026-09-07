import Foundation

// MARK: - Wire models (CONTRACT §2). Field names on the wire are snake_case;
// Swift names are camelCase via explicit CodingKeys so a rename on either side
// is a compile error rather than a silent nil.

/// One entry of `GET /api/ice` → `iceServers`. The server may send `urls` as a
/// single string or an array; both decode into `urls`.
public struct ICEServer: Codable, Equatable, Sendable {
    public var urls: [String]
    public var username: String?
    public var credential: String?

    public init(urls: [String], username: String? = nil, credential: String? = nil) {
        self.urls = urls
        self.username = username
        self.credential = credential
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let one = try? c.decode(String.self, forKey: .urls) {
            urls = [one]
        } else {
            urls = try c.decodeIfPresent([String].self, forKey: .urls) ?? []
        }
        username = try c.decodeIfPresent(String.self, forKey: .username)
        credential = try c.decodeIfPresent(String.self, forKey: .credential)
    }
}

struct ICEServersResponse: Decodable {
    var iceServers: [ICEServer]
}

/// `POST /api/translation/session`
public struct CreateSessionRequest: Codable, Equatable, Sendable {
    public var callerName: String
    public var callerLanguage: String
    public var topic: String
    public var clientId: String

    public init(callerName: String, callerLanguage: String, topic: String = "Translation Session", clientId: String) {
        self.callerName = callerName
        self.callerLanguage = callerLanguage
        self.topic = topic
        self.clientId = clientId
    }

    enum CodingKeys: String, CodingKey {
        case callerName = "caller_name"
        case callerLanguage = "caller_language"
        case topic
        case clientId = "client_id"
    }
}

struct CreateSessionResponse: Decodable {
    var sessionId: String
    var status: String?
    enum CodingKeys: String, CodingKey { case sessionId = "session_id", status }
}

/// `POST /api/translation/offer` — one per participant leg.
public struct OfferRequest: Codable, Equatable, Sendable {
    public var sessionId: String
    public var language: String
    public var name: String
    public var sdp: String
    public var type: String

    public init(sessionId: String, language: String, name: String, sdp: String, type: String = "offer") {
        self.sessionId = sessionId
        self.language = language
        self.name = name
        self.sdp = sdp
        self.type = type
    }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case language, name, sdp, type
    }
}

public struct OfferAnswer: Codable, Equatable, Sendable {
    public var pcId: String
    public var sdp: String
    public var type: String

    public init(pcId: String, sdp: String, type: String = "answer") {
        self.pcId = pcId
        self.sdp = sdp
        self.type = type
    }

    enum CodingKeys: String, CodingKey {
        case pcId = "pc_id"
        case sdp, type
    }
}

/// `POST /api/translation/ptt` — the server-side audio gate.
public enum PTTAction: String, Codable, Sendable {
    case hold
    case release
}

struct PTTRequest: Encodable {
    var pcId: String
    var action: PTTAction
    enum CodingKeys: String, CodingKey { case pcId = "pc_id", action }
}

/// The ptt response. `flushedBytes` is absent when nothing was captured on
/// release — the view model uses that to short-circuit back to `ready`.
public struct PTTResult: Codable, Equatable, Sendable {
    public var flushedBytes: Int?

    public init(flushedBytes: Int? = nil) {
        self.flushedBytes = flushedBytes
    }

    enum CodingKeys: String, CodingKey {
        case flushedBytes = "flushed_bytes"
    }
}

/// One event from `GET /api/translation/poll`. Unknown `type`s are kept but
/// ignored by consumers (CONTRACT §2 "ignore unknown types").
public struct PollEvent: Codable, Equatable, Sendable {
    public var type: String
    public var speaker: String?
    public var originalLang: String?
    public var original: String?
    public var translated: String?
    public var speakerName: String?

    public init(type: String, speaker: String? = nil, originalLang: String? = nil,
                original: String? = nil, translated: String? = nil, speakerName: String? = nil) {
        self.type = type
        self.speaker = speaker
        self.originalLang = originalLang
        self.original = original
        self.translated = translated
        self.speakerName = speakerName
    }

    enum CodingKeys: String, CodingKey {
        case type, speaker, original, translated
        case originalLang = "original_lang"
        case speakerName = "speaker_name"
    }

    public var isTurn: Bool { type == "turn" }
    public var isTurnFailed: Bool { type == "turn_failed" }
}

public struct PollResponse: Codable, Equatable, Sendable {
    public var events: [PollEvent]
    public var closed: Bool

    public init(events: [PollEvent] = [], closed: Bool = false) {
        self.events = events
        self.closed = closed
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        events = try c.decodeIfPresent([PollEvent].self, forKey: .events) ?? []
        closed = try c.decodeIfPresent(Bool.self, forKey: .closed) ?? false
    }
}

struct HangupRequest: Encodable {
    var pcId: String
    enum CodingKeys: String, CodingKey { case pcId = "pc_id" }
}

struct HangupResponse: Decodable {
    var ok: Bool?
    var found: Bool?
}

// MARK: - Entitlement

public enum Tier: String, Codable, Sendable {
    case free
    case pro
}

/// The balance payload returned by every entitlement endpoint (and inside the
/// 402 body from `/offer`).
public struct Balance: Codable, Equatable, Sendable {
    public var tier: Tier
    public var period: String?
    public var secondsTotal: Int
    public var secondsUsed: Int
    public var secondsRemaining: Int
    public var durable: Bool
    /// `false` means "metering is recorded but nobody is refused". A missing
    /// field means enforced (CONTRACT §2).
    public var enforced: Bool
    public var sandboxOk: Bool
    public var verified: Bool?

    public init(tier: Tier, period: String? = nil, secondsTotal: Int = 0, secondsUsed: Int = 0,
                secondsRemaining: Int = 0, durable: Bool = false, enforced: Bool = true,
                sandboxOk: Bool = false, verified: Bool? = nil) {
        self.tier = tier
        self.period = period
        self.secondsTotal = secondsTotal
        self.secondsUsed = secondsUsed
        self.secondsRemaining = secondsRemaining
        self.durable = durable
        self.enforced = enforced
        self.sandboxOk = sandboxOk
        self.verified = verified
    }

    enum CodingKeys: String, CodingKey {
        case tier, period, durable, enforced, verified
        case secondsTotal = "seconds_total"
        case secondsUsed = "seconds_used"
        case secondsRemaining = "seconds_remaining"
        case sandboxOk = "sandbox_ok"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // An unknown tier string is treated as free rather than failing the whole payload.
        tier = Tier(rawValue: (try c.decodeIfPresent(String.self, forKey: .tier)) ?? "free") ?? .free
        period = try c.decodeIfPresent(String.self, forKey: .period)
        secondsTotal = try c.decodeIfPresent(Int.self, forKey: .secondsTotal) ?? 0
        secondsUsed = try c.decodeIfPresent(Int.self, forKey: .secondsUsed) ?? 0
        secondsRemaining = try c.decodeIfPresent(Int.self, forKey: .secondsRemaining) ?? 0
        durable = try c.decodeIfPresent(Bool.self, forKey: .durable) ?? false
        enforced = try c.decodeIfPresent(Bool.self, forKey: .enforced) ?? true
        sandboxOk = try c.decodeIfPresent(Bool.self, forKey: .sandboxOk) ?? false
        verified = try c.decodeIfPresent(Bool.self, forKey: .verified)
    }

    public var isPro: Bool { tier == .pro }
}

public enum StorePlatform: String, Codable, Sendable {
    case ios
    case android
}

public struct ActivateRequest: Codable, Equatable, Sendable {
    public var subject: String
    public var platform: StorePlatform
    public var receipt: String
    /// `true` with an empty receipt is a verified "no subscription" (downgrades);
    /// `false` means "could not ask the store" and must NOT downgrade.
    public var storeReachable: Bool

    public init(subject: String, platform: StorePlatform = .ios, receipt: String, storeReachable: Bool) {
        self.subject = subject
        self.platform = platform
        self.receipt = receipt
        self.storeReachable = storeReachable
    }

    enum CodingKeys: String, CodingKey {
        case subject, platform, receipt
        case storeReachable = "store_reachable"
    }
}

struct ConsumeRequest: Encodable {
    var subject: String
    var sessionId: String
    var sessionSeconds: Int
    enum CodingKeys: String, CodingKey {
        case subject
        case sessionId = "session_id"
        case sessionSeconds = "session_seconds"
    }
}

// MARK: - History (server side)

/// A row of `GET /api/translation/sessions`.
public struct SessionSummary: Codable, Equatable, Sendable, Identifiable {
    public var sessionId: String
    public var callerName: String?
    public var topic: String?
    public var lang: String?
    public var status: String?
    public var duration: Int?
    public var participantCount: Int?

    public var id: String { sessionId }

    public init(sessionId: String, callerName: String? = nil, topic: String? = nil, lang: String? = nil,
                status: String? = nil, duration: Int? = nil, participantCount: Int? = nil) {
        self.sessionId = sessionId
        self.callerName = callerName
        self.topic = topic
        self.lang = lang
        self.status = status
        self.duration = duration
        self.participantCount = participantCount
    }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case callerName = "caller_name"
        case topic, lang, status, duration
        case participantCount = "participant_count"
    }

    public var isLive: Bool { status == "live" || status == "active" }
}

struct SessionListResponse: Decodable {
    var sessions: [SessionSummary]
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sessions = try c.decodeIfPresent([SessionSummary].self, forKey: .sessions) ?? []
    }
    enum CodingKeys: String, CodingKey { case sessions }
}

/// One transcript line as persisted (the poll `turn` shape; offline sessions
/// fill `speakerName` with the side name).
public struct TranscriptTurn: Codable, Equatable, Sendable {
    public var speaker: String?
    public var speakerName: String?
    public var originalLang: String?
    public var original: String
    public var translated: String

    public init(speaker: String? = nil, speakerName: String? = nil, originalLang: String? = nil,
                original: String, translated: String) {
        self.speaker = speaker
        self.speakerName = speakerName
        self.originalLang = originalLang
        self.original = original
        self.translated = translated
    }

    enum CodingKeys: String, CodingKey {
        case speaker, original, translated
        case speakerName = "speaker_name"
        case originalLang = "original_lang"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        speaker = try c.decodeIfPresent(String.self, forKey: .speaker)
        speakerName = try c.decodeIfPresent(String.self, forKey: .speakerName)
        originalLang = try c.decodeIfPresent(String.self, forKey: .originalLang)
        original = try c.decodeIfPresent(String.self, forKey: .original) ?? ""
        translated = try c.decodeIfPresent(String.self, forKey: .translated) ?? ""
    }
}

/// `GET /api/translation/session/{id}` (and the shape the local store hands back).
public struct SessionDetail: Codable, Equatable, Sendable {
    public var sessionId: String
    public var callerName: String?
    public var lang: String?
    public var topic: String?
    public var status: String?
    public var transcript: [TranscriptTurn]
    public var participantA: String?
    public var participantB: String?
    public var duration: Int?

    public init(sessionId: String, callerName: String? = nil, lang: String? = nil, topic: String? = nil,
                status: String? = nil, transcript: [TranscriptTurn] = [], participantA: String? = nil,
                participantB: String? = nil, duration: Int? = nil) {
        self.sessionId = sessionId
        self.callerName = callerName
        self.lang = lang
        self.topic = topic
        self.status = status
        self.transcript = transcript
        self.participantA = participantA
        self.participantB = participantB
        self.duration = duration
    }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case callerName = "caller_name"
        case lang, topic, status, transcript, duration
        case participantA = "participant_a"
        case participantB = "participant_b"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sessionId = try c.decode(String.self, forKey: .sessionId)
        callerName = try c.decodeIfPresent(String.self, forKey: .callerName)
        lang = try c.decodeIfPresent(String.self, forKey: .lang)
        topic = try c.decodeIfPresent(String.self, forKey: .topic)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        transcript = try c.decodeIfPresent([TranscriptTurn].self, forKey: .transcript) ?? []
        participantA = try c.decodeIfPresent(String.self, forKey: .participantA)
        participantB = try c.decodeIfPresent(String.self, forKey: .participantB)
        duration = try c.decodeIfPresent(Int.self, forKey: .duration)
    }
}

/// Error body: `{ "error": "..." }`, optionally with a balance on 402.
struct ErrorBody: Decodable {
    var error: String?
    var balance: Balance?
}
