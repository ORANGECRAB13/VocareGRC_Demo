import Foundation
import VocaKit

// MARK: - Cross-owner models (CONTRACT §6)

/// Which half of the split screen. A is the phone's owner (bottom half),
/// B is the person across the table (top half, rotated).
public enum Side: String, Hashable, Sendable, CaseIterable {
    case a = "A"
    case b = "B"

    public var other: Side { self == .a ? .b : .a }
}

/// One bubble on a live panel. `mine` means "the reader's own words" — rendered
/// as "You said" with the original; otherwise "Translated" with the source
/// underneath (see JSX `LiveTurn`).
public struct TurnView: Identifiable, Equatable, Sendable {
    public let id: Int
    public let original: String
    public let translated: String
    public let mine: Bool

    public init(id: Int, original: String, translated: String, mine: Bool) {
        self.id = id
        self.original = original
        self.translated = translated
        self.mine = mine
    }
}

public struct SideState: Equatable, Sendable {
    public var name: String
    public var langCode: String
    public var langLabel: String
    public var pressing: Bool
    public var disabled: Bool
    /// Dims the mic button (JSX `dimmed`) — set while this side is translating.
    public var dimmed: Bool
    public var status: String
    public var turns: [TurnView]
    /// Small italic note under the turns: connection state while connecting, or
    /// the live partial transcript on the speaker's own panel offline.
    public var note: String?

    public init(name: String, langCode: String, langLabel: String, pressing: Bool = false,
                disabled: Bool = true, dimmed: Bool = false, status: String = "",
                turns: [TurnView] = [], note: String? = nil) {
        self.name = name
        self.langCode = langCode
        self.langLabel = langLabel
        self.pressing = pressing
        self.disabled = disabled
        self.dimmed = dimmed
        self.status = status
        self.turns = turns
        self.note = note
    }
}

public enum SessionError: Equatable, Sendable {
    /// Microphone (or speech recognition) permission denied → error screen `mic`.
    case mic
    /// ICE/offer/connection failure → error screen `network`.
    case network
}

/// Why a session reached `.ended`. The host routes `exhausted` to the paywall.
public enum EndReason: Equatable, Sendable {
    case user
    case serverClosed
    case exhausted
}

public enum LivePhase: Equatable, Sendable {
    case connecting
    case live
    case ended(EndReason)
    case error(SessionError)

    public var isLive: Bool { self == .live }
    public var isEnded: Bool {
        if case .ended = self { return true }
        return false
    }
}

public struct LiveState: Equatable, Sendable {
    public var sideA: SideState
    public var sideB: SideState
    public var elapsed: TimeInterval
    public var phase: LivePhase
    /// Centre-bar badge (`ON DEVICE`); nil shows the blinking live dot.
    public var badge: String?
    /// Error strip between the centre bar and panel A (offline notices).
    public var notice: String?

    public init(sideA: SideState, sideB: SideState, elapsed: TimeInterval = 0,
                phase: LivePhase = .connecting, badge: String? = nil, notice: String? = nil) {
        self.sideA = sideA
        self.sideB = sideB
        self.elapsed = elapsed
        self.phase = phase
        self.badge = badge
        self.notice = notice
    }

    public var elapsedClock: String { LiveState.formatClock(elapsed) }

    /// JSX `formatClock`: mm:ss, zero padded, never negative.
    public static func formatClock(_ seconds: TimeInterval) -> String {
        let n = max(0, Int(seconds))
        return String(format: "%02d:%02d", n / 60, n % 60)
    }
}

/// One line of the transcript as the session keeps it (the poll `turn` event
/// shape for cloud; `{side, from, to, original, translated}` for offline).
public struct SessionTurn: Equatable, Sendable, Codable {
    public var speaker: String?
    public var speakerName: String?
    public var originalLang: String
    public var targetLang: String?
    public var original: String
    public var translated: String

    public init(speaker: String? = nil, speakerName: String? = nil, originalLang: String,
                targetLang: String? = nil, original: String, translated: String) {
        self.speaker = speaker
        self.speakerName = speakerName
        self.originalLang = originalLang
        self.targetLang = targetLang
        self.original = original
        self.translated = translated
    }
}

// MARK: - Language table (labels for the panels; flags live in VocaKit's picker)

public enum SessionLanguages {
    public static let labels: [String: String] = [
        "en": "English", "zh": "Mandarin", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean",
        "es": "Spanish", "fr": "French", "de": "German", "ar": "Arabic", "hi": "Hindi",
        "fil": "Filipino (Tagalog)",
    ]

    public static func label(_ code: String) -> String { labels[code] ?? code }

    /// Speech and synthesis want full locales, not bare language codes (JSX `SPEECH_LOCALE`).
    public static let speechLocales: [String: String] = [
        "en": "en-US", "zh": "zh-CN", "yue": "zh-HK", "ja": "ja-JP", "ko": "ko-KR",
        "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "ar": "ar-SA", "hi": "hi-IN", "fil": "fil-PH",
    ]

    public static func speechLocale(_ code: String) -> String { speechLocales[code] ?? code }

    /// Languages with a first-party on-device model on iOS (CONTRACT §4).
    public static let offlineCodes: Set<String> = ["en", "zh", "ja", "ko", "es", "fr", "de", "ar", "hi"]

    public static func canTranslateOffline(_ a: String, _ b: String) -> Bool {
        !a.isEmpty && !b.isEmpty && a != b && offlineCodes.contains(a) && offlineCodes.contains(b)
    }
}

// MARK: - Session configuration (CONTRACT §6)

/// What the host hands a live session. `budgetSeconds` is `.infinity` when the
/// tier is unmetered (`enforced:false`) — the cloud view model ends the call
/// the moment `elapsed` reaches it.
public struct SessionConfig: Equatable, Sendable {
    public var langA: String
    public var langB: String
    public var nameA: String
    public var nameB: String
    public var clientId: String
    public var budgetSeconds: TimeInterval

    public init(langA: String, langB: String, nameA: String = "Person A", nameB: String = "Person B",
                clientId: String, budgetSeconds: TimeInterval = .infinity) {
        self.langA = langA
        self.langB = langB
        self.nameA = nameA
        self.nameB = nameB
        self.clientId = clientId
        self.budgetSeconds = budgetSeconds
    }
}

/// Per-side push-to-talk state (JSX `turnStateA/B`).
public enum TurnState: Equatable, Sendable {
    case ready
    case recording
    case translating
}
