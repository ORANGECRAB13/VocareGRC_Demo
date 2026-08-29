import Capacitor
import Foundation
import Translation

/// On-device translation via Apple's Translation framework.
///
/// The framework has existed since iOS 17.4, but until iOS 26 a TranslationSession
/// could only be obtained from the SwiftUI `.translationTask` modifier — unusable
/// from a Capacitor plugin without hosting an invisible SwiftUI view over the
/// webview. iOS 26 added `init(installedSource:target:)`, which is headless. So
/// every entry point here is gated on iOS 26 and reports itself unavailable below
/// it rather than pretending.
///
/// Two limits worth knowing, both Apple's:
///   - A session built with this initializer CANNOT request language downloads.
///     It simply fails when a pack is missing. Packs are installed by the user in
///     Settings > Apps > Translate > Downloaded Languages. `prepare` therefore
///     reports what is missing instead of fetching it.
///   - The framework does not work in the iOS Simulator. Test on a device.
@objc(OfflineTranslatePlugin)
public final class OfflineTranslatePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "OfflineTranslatePlugin"
    public let jsName = "VocareOfflineTranslate"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "supportedLanguages", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pairStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "translate", returnType: CAPPluginReturnPromise)
    ]

    /// The app's language codes are bare ISO codes; Apple wants a full language
    /// identifier. Chinese in particular must name a script — Apple ships Mandarin
    /// in Simplified and Traditional and has no Cantonese model at all, so "zh"
    /// alone is ambiguous.
    private static let identifierOverrides: [String: String] = [
        "zh": "zh-Hans"
    ]

    private static func language(for code: String) -> Locale.Language {
        Locale.Language(identifier: identifierOverrides[code] ?? code)
    }

    // MARK: - Availability

    @objc func isAvailable(_ call: CAPPluginCall) {
        if #available(iOS 26.0, *) {
            call.resolve(["available": true])
        } else {
            call.resolve([
                "available": false,
                "reason": "On-device translation needs iOS 26 or later."
            ])
        }
    }

    @objc func supportedLanguages(_ call: CAPPluginCall) {
        guard #available(iOS 26.0, *) else {
            call.resolve(["languages": []])
            return
        }
        Task {
            let languages = await LanguageAvailability().supportedLanguages
            // Report bare codes so the JS layer can compare them against its own
            // language table without knowing about scripts or regions.
            let codes = Set(languages.compactMap { $0.languageCode?.identifier })
            call.resolve(["languages": Array(codes).sorted()])
        }
    }

    // MARK: - Pair status

    /// installed  — both packs present, translation will work offline right now
    /// supported  — Apple has a model but the user has not downloaded it
    /// unsupported — no model for this pair (e.g. anything involving Cantonese)
    @objc func pairStatus(_ call: CAPPluginCall) {
        guard let source = call.getString("source"), let target = call.getString("target") else {
            call.reject("source and target are required.")
            return
        }
        guard #available(iOS 26.0, *) else {
            call.resolve(["status": "unsupported", "reason": "Requires iOS 26 or later."])
            return
        }
        Task {
            let status = await LanguageAvailability().status(
                from: Self.language(for: source),
                to: Self.language(for: target)
            )
            let value: String
            switch status {
            case .installed: value = "installed"
            case .supported: value = "supported"
            case .unsupported: value = "unsupported"
            @unknown default: value = "unsupported"
            }
            call.resolve(["status": value])
        }
    }

    // MARK: - Translate

    @objc func translate(_ call: CAPPluginCall) {
        guard let text = call.getString("text"), !text.isEmpty,
              let source = call.getString("source"),
              let target = call.getString("target") else {
            call.reject("text, source and target are required.")
            return
        }
        guard #available(iOS 26.0, *) else {
            call.reject("On-device translation needs iOS 26 or later.")
            return
        }
        Task {
            do {
                let session = try TranslationSession(
                    installedSource: Self.language(for: source),
                    target: Self.language(for: target)
                )
                let response = try await session.translate(text)
                call.resolve(["text": response.targetText])
            } catch {
                // The overwhelmingly common cause is a language pack the user has
                // not installed — this initializer cannot download one. Say which
                // pair failed so the caller can send them to Settings or fall back
                // to the server.
                call.reject(
                    "On-device translation is unavailable for \(source) to \(target). "
                    + "The language may not be downloaded on this device.",
                    nil,
                    error
                )
            }
        }
    }
}
