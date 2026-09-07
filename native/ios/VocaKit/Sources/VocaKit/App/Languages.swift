import Foundation

/// The JSX `LANGUAGES` table with flags. VOCA `main` withdrew Cantonese from
/// the picker (it has no on-device model anywhere and the cloud path is
/// Mandarin-only in practice), so `pickerLanguages` omits `yue` while `all`
/// keeps it so history rows written before the change still label correctly.
public struct Language: Identifiable, Equatable, Hashable, Sendable {
    public let code: String
    public let flag: String
    public let label: String

    public var id: String { code }

    public init(code: String, flag: String, label: String) {
        self.code = code
        self.flag = flag
        self.label = label
    }
}

public enum Languages {
    public static let all: [Language] = [
        Language(code: "en", flag: "🇺🇸", label: "English"),
        Language(code: "zh", flag: "🇨🇳", label: "Mandarin"),
        Language(code: "yue", flag: "🇭🇰", label: "Cantonese"),
        Language(code: "ja", flag: "🇯🇵", label: "Japanese"),
        Language(code: "ko", flag: "🇰🇷", label: "Korean"),
        Language(code: "es", flag: "🇪🇸", label: "Spanish"),
        Language(code: "fr", flag: "🇫🇷", label: "French"),
        Language(code: "de", flag: "🇩🇪", label: "German"),
        Language(code: "ar", flag: "🇦🇪", label: "Arabic"),
        Language(code: "hi", flag: "🇮🇳", label: "Hindi"),
        Language(code: "fil", flag: "🇵🇭", label: "Filipino (Tagalog)"),
    ]

    /// What the picker offers (VOCA main: no Cantonese).
    public static let pickerLanguages: [Language] = all.filter { $0.code != "yue" }

    /// JSX `getLang`: unknown codes fall back to a globe.
    public static func get(_ code: String) -> Language {
        all.first { $0.code == code } ?? Language(code: code, flag: "🌐", label: code)
    }

    /// Languages with a first-party on-device translation model on iOS (CONTRACT §4).
    public static let offlineCodes: [String] = ["en", "zh", "ja", "ko", "es", "fr", "de", "ar", "hi"]

    /// Whether this pair could be translated with no network on iOS. Says nothing
    /// about whether the packs are installed — `OfflineAvailability` answers that.
    public static func canTranslateOffline(_ a: String, _ b: String) -> Bool {
        !a.isEmpty && !b.isEmpty && a != b && offlineCodes.contains(a) && offlineCodes.contains(b)
    }

    /// JSX `LangScreen` search: label or code contains the query, case-insensitive.
    public static func search(_ query: String) -> [Language] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return pickerLanguages }
        return pickerLanguages.filter { $0.label.lowercased().contains(q) || $0.code.lowercased().contains(q) }
    }
}

// MARK: - Formatting helpers (JSX formatMinutes / formatDuration / formatClock)

public enum VocaFormat {
    /// Tier chip: `12 min` at ≥10 minutes, else `m:ss`.
    public static func minutes(_ seconds: Int) -> String {
        let clamped = max(0, seconds)
        let mins = clamped / 60
        let secs = clamped % 60
        if mins >= 10 { return "\(mins) min" }
        return "\(mins):" + String(format: "%02d", secs)
    }

    /// History rows: `3m 12s` or `45s`.
    public static func duration(_ seconds: Int?) -> String {
        guard let seconds else { return "" }
        let m = seconds / 60
        let s = seconds % 60
        return m > 0 ? "\(m)m \(s)s" : "\(s)s"
    }

    /// Live-bar clock: tabular MM:SS, never negative.
    public static func clock(_ seconds: Int) -> String {
        let n = max(0, seconds)
        return String(format: "%02d:%02d", n / 60, n % 60)
    }
}
