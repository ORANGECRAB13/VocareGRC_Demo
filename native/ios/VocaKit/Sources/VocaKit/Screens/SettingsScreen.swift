import SwiftUI

/// JSX `SettingsScreen`: session toggles, subscription, offline languages with
/// install buttons, legal links.
public struct SettingsScreen: View {
    @ObservedObject var preferences: Preferences
    @ObservedObject var entitlements: EntitlementStore
    let offline: OfflineAvailability
    let privacyURL: URL
    let onUpgrade: () -> Void
    let onStartOffline: () -> Void

    public init(preferences: Preferences, entitlements: EntitlementStore, offline: OfflineAvailability, privacyURL: URL,
                onUpgrade: @escaping () -> Void, onStartOffline: @escaping () -> Void) {
        self.preferences = preferences
        self.entitlements = entitlements
        self.offline = offline
        self.privacyURL = privacyURL
        self.onUpgrade = onUpgrade
        self.onStartOffline = onStartOffline
    }

    public var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                ScreenTitle("Settings")
                VStack(alignment: .leading, spacing: 20) {
                    group("Session") {
                        ToggleRow("Automated notes", hint: "Summarise each session when it ends", isOn: $preferences.notes)
                        divider
                        ToggleRow("Save transcripts", hint: "Keep full text on this device", isOn: $preferences.saveTranscripts)
                        divider
                        ToggleRow("Speak translations aloud", hint: "Play synthesised voice on the other half", isOn: $preferences.autoplay)
                    }
                    group("Accessibility") {
                        ToggleRow("Haptic confirmation", hint: "Buzz on press and release", isOn: $preferences.haptics)
                        divider
                        ToggleRow("Larger transcript text", hint: "Increase live text size by 20%", isOn: $preferences.largeText)
                    }
                    SubscriptionSection(entitlements: entitlements, onUpgrade: onUpgrade)
                    OfflineLanguagesSection(offline: offline, onStart: onStartOffline)
                    group("Legal") {
                        LinkRow("Privacy policy", hint: "How microphone audio and transcripts are handled", url: privacyURL)
                        divider
                        LinkRow("Terms of use", hint: "Apple's standard licensed application terms",
                                url: URL(string: "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")!)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 6)
                .padding(.bottom, 96)
            }
        }
        .background(VocaTheme.ground.ignoresSafeArea())
    }

    private var divider: some View { Rectangle().fill(VocaTheme.ink.opacity(0.06)).frame(height: 1) }

    private func group<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title)
            VocaCard { VStack(spacing: 0, content: content) }
        }
    }
}

/// JSX `SubscriptionSection`. Cancellation is deliberately not offered: the
/// store requires it in the user's account.
public struct SubscriptionSection: View {
    @ObservedObject var entitlements: EntitlementStore
    let onUpgrade: () -> Void

    public init(entitlements: EntitlementStore, onUpgrade: @escaping () -> Void) {
        self.entitlements = entitlements
        self.onUpgrade = onUpgrade
    }

    public var body: some View {
        let pro = entitlements.isPro
        let left = entitlements.secondsLeft
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader("Subscription")
            VocaCard {
                VStack(spacing: 0) {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(pro ? "Voca Pro" : "Free").vocaDisplay(20)
                            if pro {
                                Text("\(VocaFormat.minutes(left)) left")
                                    .vocaMicroLabel(10, color: left > 0 ? VocaTheme.greenDeep : VocaTheme.error, em: 0.1)
                            }
                        }
                        Text(pro
                             ? "\(PurchasesService.planMinutes) minutes of cloud translation each month, then on-device translation stays available."
                             : "On-device translation, free and unlimited, for the language pairs your phone can do without a network.")
                            .vocaBody(12.5, color: VocaTheme.inkMute).lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                        if pro {
                            ProgressBar(fraction: Double(max(0, left)) / Double(PurchasesService.planMinutes * 60))
                                .padding(.top, 7)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 15)
                    Rectangle().fill(VocaTheme.ink.opacity(0.06)).frame(height: 1)
                    Button(action: onUpgrade) {
                        HStack(spacing: 12) {
                            Text(pro ? "Manage or restore purchase" : "Upgrade to Pro — \(entitlements.displayPrice)/mo")
                                .vocaBody(14.5, weight: .semibold, color: pro ? VocaTheme.ink : VocaTheme.violet)
                            Spacer(minLength: 0)
                            Icon(.chevron, size: 16, color: VocaTheme.ink.opacity(0.3))
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

/// JSX `OfflineTranslationSection`: status against English (the pivot), with
/// in-app installs via `prepare` (both directions).
public struct OfflineLanguagesSection: View {
    let offline: OfflineAvailability
    let onStart: () -> Void

    @State private var statuses: [String: PairStatus]?
    @State private var busy: String?
    @State private var error: String?

    public init(offline: OfflineAvailability, onStart: @escaping () -> Void) {
        self.offline = offline
        self.onStart = onStart
    }

    private var supported: [String] { Languages.offlineCodes.filter { $0 != "en" } }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader("Offline translation")
            VocaCard {
                VStack(spacing: 0) {
                    Text("Download a language to translate it to and from English with no connection. Each is a few hundred MB, so use Wi-Fi. Cantonese has no offline model on any phone and always needs a connection.")
                        .vocaBody(12.5, color: VocaTheme.ink.opacity(0.62)).lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 15).padding(.vertical, 13)
                    ForEach(supported, id: \.self) { code in
                        Rectangle().fill(VocaTheme.ink.opacity(0.06)).frame(height: 1)
                        row(code)
                    }
                }
            }
            Button(action: onStart) {
                HStack(spacing: 8) {
                    Icon(.mic, size: 17, color: VocaTheme.ink)
                    Text("Start an offline session").vocaBody(15, weight: .semibold)
                }
                .frame(maxWidth: .infinity).frame(height: 52)
                .background(VocaTheme.surface)
                .clipShape(RoundedRectangle(cornerRadius: VocaTheme.cardRadius, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: VocaTheme.cardRadius, style: .continuous).stroke(VocaTheme.ink.opacity(0.14)))
            }
            .buttonStyle(.plain)
            .padding(.top, 10)
            if let error {
                Text(error).vocaBody(12, color: VocaTheme.error).padding(.top, 6)
            }
        }
        .task { await refresh() }
    }

    private func row(_ code: String) -> some View {
        let lang = Languages.get(code)
        let status = statuses?[code]
        return HStack(spacing: 12) {
            Text(lang.flag).font(.system(size: 17))
            Text(lang.label).vocaBody(14.5, weight: .semibold)
            Spacer(minLength: 0)
            if statuses == nil {
                Text("Checking…").vocaBody(12, color: VocaTheme.ink.opacity(0.4))
            } else {
                switch status {
                case .installed:
                    Text("On device").vocaBody(12, weight: .semibold, color: VocaTheme.greenDeep)
                case .supported:
                    Button { install(code) } label: {
                        Text(busy == code ? "Downloading…" : "Download")
                            .vocaBody(12.5, weight: .semibold, color: busy == code ? VocaTheme.ink.opacity(0.4) : VocaTheme.violet)
                    }
                    .buttonStyle(.plain)
                    .disabled(busy != nil)
                default:
                    Text("Not available").vocaBody(12, color: VocaTheme.ink.opacity(0.35))
                }
            }
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 12)
    }

    private func refresh() async {
        var result: [String: PairStatus] = [:]
        for code in supported {
            result[code] = await offline.pairStatus("en", code)
        }
        statuses = result
    }

    private func install(_ code: String) {
        busy = code
        error = nil
        Task {
            do {
                _ = try await offline.prepare("en", code)
            } catch {
                self.error = "Could not download \(Languages.get(code).label). Check your connection and try again."
            }
            await refresh()
            busy = nil
        }
    }
}
