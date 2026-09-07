import Foundation
import Combine

/// Everything the JSX kept in localStorage: mic consent, the offline switch
/// tri-state, the language pair and the Settings toggles. `UserDefaults`-backed,
/// observable, and injectable (tests pass a throwaway suite).
@MainActor
public final class Preferences: ObservableObject {
    public enum Key {
        public static let micConsent = "vocare.micConsent.v1"
        public static let offlineMode = "vocare.offlineMode"
        public static let langA = "vocare.langA"
        public static let langB = "vocare.langB"
        public static let notes = "vocare.settings.notes"
        public static let saveTranscripts = "vocare.settings.save"
        public static let autoplay = "vocare.settings.autoplay"
        public static let haptics = "vocare.settings.haptics"
        public static let largeText = "vocare.settings.large"
    }

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        micConsent = defaults.string(forKey: Key.micConsent) == "granted"
        offlineMode = defaults.object(forKey: Key.offlineMode) as? Bool
        langA = defaults.string(forKey: Key.langA) ?? "en"
        langB = defaults.string(forKey: Key.langB) ?? "zh"
        notes = defaults.object(forKey: Key.notes) as? Bool ?? true
        saveTranscripts = defaults.object(forKey: Key.saveTranscripts) as? Bool ?? true
        autoplay = defaults.object(forKey: Key.autoplay) as? Bool ?? true
        haptics = defaults.object(forKey: Key.haptics) as? Bool ?? true
        largeText = defaults.object(forKey: Key.largeText) as? Bool ?? false
    }

    /// Recorded once the disclosure sheet is accepted; gates the OS mic prompt.
    @Published public var micConsent: Bool {
        didSet { defaults.set(micConsent ? "granted" : nil, forKey: Key.micConsent) }
    }

    /// Tri-state: nil until the user touches the switch, then sticks (JSX `readOfflinePref`).
    @Published public var offlineMode: Bool? {
        didSet {
            if let offlineMode { defaults.set(offlineMode, forKey: Key.offlineMode) }
            else { defaults.removeObject(forKey: Key.offlineMode) }
        }
    }

    @Published public var langA: String { didSet { defaults.set(langA, forKey: Key.langA) } }
    @Published public var langB: String { didSet { defaults.set(langB, forKey: Key.langB) } }

    @Published public var notes: Bool { didSet { defaults.set(notes, forKey: Key.notes) } }
    @Published public var saveTranscripts: Bool { didSet { defaults.set(saveTranscripts, forKey: Key.saveTranscripts) } }
    @Published public var autoplay: Bool { didSet { defaults.set(autoplay, forKey: Key.autoplay) } }
    @Published public var haptics: Bool { didSet { defaults.set(haptics, forKey: Key.haptics) } }
    @Published public var largeText: Bool { didSet { defaults.set(largeText, forKey: Key.largeText) } }

    public func swapLanguages() {
        let a = langA
        langA = langB
        langB = a
    }
}
