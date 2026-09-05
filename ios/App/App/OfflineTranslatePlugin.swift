import Capacitor
import Foundation
import SwiftUI
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
///     It simply fails when a pack is missing. Downloads are only offered by
///     `TranslationSession.prepareTranslation()`, which exists solely inside the
///     SwiftUI `.translationTask` modifier — so `prepare` below hosts an
///     invisible SwiftUI view over the webview purely to reach it. That is the
///     only way to install a pack without sending the user to
///     Settings > Apps > Translate > Downloaded Languages.
///   - The framework does not work in the iOS Simulator. Test on a device.
@objc(OfflineTranslatePlugin)
public final class OfflineTranslatePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "OfflineTranslatePlugin"
    public let jsName = "VocareOfflineTranslate"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "supportedLanguages", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pairStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "prepare", returnType: CAPPluginReturnPromise),
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

    // MARK: - Prepare (download)

    /// Ask Apple to install the language pack for one pair.
    ///
    /// `prepareTranslation()` presents Apple's own download sheet — we never draw
    /// it, and never see the progress. It is reachable only from the SwiftUI
    /// `.translationTask` modifier, so this presents a fully transparent hosting
    /// controller for the duration of the prompt and tears it down afterwards.
    /// The controller must actually be in the window hierarchy for the sheet to
    /// appear, which is why it is presented rather than merely instantiated.
    ///
    /// Resolves `{ installed: true }` once the pack is present, or
    /// `{ installed: false, cancelled: true }` if the user declined. A decline is
    /// not an error — it is the user saying no, and the caller falls back to the
    /// server.
    @objc func prepare(_ call: CAPPluginCall) {
        guard let source = call.getString("source"), let target = call.getString("target") else {
            call.reject("source and target are required.")
            return
        }
        guard #available(iOS 18.0, *) else {
            call.resolve(["installed": false, "reason": "Requires iOS 18 or later."])
            return
        }

        DispatchQueue.main.async { [weak self] in
            guard let presenter = self?.bridge?.viewController else {
                call.reject("No view controller available to present the download prompt.")
                return
            }

            var host: UIViewController?
            // Guards against the completion firing twice — `.translationTask` can
            // re-run when the configuration is invalidated, and resolving a
            // CAPPluginCall twice is a hard error.
            var settled = false

            let finish: (Bool, Error?) -> Void = { installed, error in
                guard !settled else { return }
                settled = true
                DispatchQueue.main.async {
                    host?.dismiss(animated: false)
                    host = nil
                    if let error {
                        // A cancelled sheet throws. Report it as a decline rather
                        // than a failure so the UI can stay calm about it.
                        CAPLog.print("[OfflineTranslate] prepare failed: \(error)")
                        call.resolve(["installed": false, "cancelled": true])
                    } else {
                        call.resolve(["installed": installed])
                    }
                }
            }

            let view = TranslationPreparerView(
                source: Self.language(for: source),
                target: Self.language(for: target),
                onFinish: finish
            )
            let controller = UIHostingController(rootView: view)
            controller.view.backgroundColor = .clear
            controller.modalPresentationStyle = .overFullScreen
            controller.modalTransitionStyle = .crossDissolve
            host = controller
            presenter.present(controller, animated: false)
        }
    }
}

/// Invisible host for `.translationTask`. Draws nothing; exists only because
/// `prepareTranslation()` has no non-SwiftUI entry point.
@available(iOS 18.0, *)
private struct TranslationPreparerView: View {
    let source: Locale.Language
    let target: Locale.Language
    let onFinish: (Bool, Error?) -> Void

    @State private var configuration: TranslationSession.Configuration?

    var body: some View {
        Color.clear
            .translationTask(configuration) { session in
                do {
                    try await session.prepareTranslation()
                    onFinish(true, nil)
                } catch {
                    onFinish(false, error)
                }
            }
            .onAppear {
                configuration = TranslationSession.Configuration(source: source, target: target)
            }
    }
}
