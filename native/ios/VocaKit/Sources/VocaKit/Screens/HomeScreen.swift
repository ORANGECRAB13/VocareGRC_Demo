import SwiftUI

/// JSX `WelcomeScreen`: logo, tier chip, mascot, offline switch, language
/// pair card, Start.
public struct HomeScreen: View {
    @ObservedObject var entitlements: EntitlementStore
    @ObservedObject var preferences: Preferences
    let offline: OfflineAvailability
    let onStart: () -> Void
    let onPick: (Screen.PickSide) -> Void
    let onUpgrade: () -> Void

    public init(entitlements: EntitlementStore, preferences: Preferences, offline: OfflineAvailability,
                onStart: @escaping () -> Void, onPick: @escaping (Screen.PickSide) -> Void, onUpgrade: @escaping () -> Void) {
        self.entitlements = entitlements
        self.preferences = preferences
        self.offline = offline
        self.onStart = onStart
        self.onPick = onPick
        self.onUpgrade = onUpgrade
    }

    public var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, 26)
                .padding(.top, 30)

            Spacer(minLength: 8)
            VocaMascot()
                .frame(height: 180)
            Text("Ready when you are")
                .font(VocaTheme.display(15.5, weight: .medium))
                .foregroundStyle(VocaTheme.ink.opacity(0.5))
                .padding(.top, 16)
            Spacer(minLength: 8)

            VStack(spacing: 0) {
                OfflineSwitch(
                    isOn: Binding(get: { entitlements.offlineMode }, set: { entitlements.setOfflineMode($0) }),
                    langA: preferences.langA, langB: preferences.langB, offline: offline
                )
                .padding(.bottom, 8)

                languageCard

                PrimaryButton("Start a session", icon: .mic, action: onStart)
                    .padding(.top, 12)

                Text("Hold your half to talk")
                    .vocaMicroLabel(10, color: VocaTheme.ink.opacity(0.35))
                    .padding(.top, 14)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(homeBackground)
    }

    private var homeBackground: some View {
        ZStack {
            VocaTheme.ground
            RadialGradient(colors: [VocaTheme.surface2, VocaTheme.ground.opacity(0)],
                           center: UnitPoint(x: 0.5, y: 0.06), startRadius: 0, endRadius: 420)
            RadialGradient(colors: [Color(hex: 0xEBF6F0), VocaTheme.ground.opacity(0)],
                           center: UnitPoint(x: 0.88, y: 0.96), startRadius: 0, endRadius: 320)
        }
        .ignoresSafeArea()
    }

    private var header: some View {
        HStack(spacing: 9) {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(VocaTheme.violet)
                .frame(width: 26, height: 26)
                .overlay(HStack(spacing: 4) {
                    Circle().fill(.white).frame(width: 4, height: 4)
                    Circle().fill(.white).frame(width: 4, height: 4)
                })
            Text("Voca").font(VocaTheme.display(17)).tracking(-0.17).foregroundStyle(VocaTheme.ink)
            Spacer()
            TierChip(isPro: entitlements.isPro, secondsLeft: entitlements.secondsLeft, action: onUpgrade)
        }
    }

    private var languageCard: some View {
        HStack(spacing: 8) {
            pairButton(label: "Person A", code: preferences.langA, tint: VocaTheme.inkA07,
                       ink: VocaTheme.violetDeep, alignment: .leading) { onPick(.a) }
            Button { withAnimation { preferences.swapLanguages() } } label: {
                Icon(.swap, size: 18, color: VocaTheme.ink.opacity(0.4)).frame(width: 38)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Swap languages")
            pairButton(label: "Person B", code: preferences.langB, tint: VocaTheme.inkB09,
                       ink: VocaTheme.greenDeep, alignment: .trailing) { onPick(.b) }
        }
        .padding(8)
        .background(VocaTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: VocaTheme.cardRadiusLarge, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: VocaTheme.cardRadiusLarge, style: .continuous).stroke(VocaTheme.hairline))
        .shadow(color: VocaTheme.ink.opacity(0.12), radius: 15, y: 10)
    }

    private func pairButton(label: String, code: String, tint: Color, ink: Color,
                            alignment: HorizontalAlignment, action: @escaping () -> Void) -> some View {
        let lang = Languages.get(code)
        return Button(action: action) {
            VStack(alignment: alignment, spacing: 5) {
                Text(label).vocaMicroLabel(9.5, color: ink, em: 0.12)
                HStack(spacing: 6) {
                    if alignment == .trailing { Text(lang.flag).font(.system(size: 15)) }
                    Text(lang.label).vocaBody(16, weight: .semibold).lineLimit(1)
                    if alignment == .leading { Text(lang.flag).font(.system(size: 15)) }
                }
            }
            .frame(maxWidth: .infinity, alignment: alignment == .leading ? .leading : .trailing)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(tint)
            .clipShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(label): \(lang.label)")
    }
}

/// A subscriber sees what is left before starting; a free user sees the one
/// route to the cloud engine.
public struct TierChip: View {
    let isPro: Bool
    let secondsLeft: Int
    let action: () -> Void

    public init(isPro: Bool, secondsLeft: Int, action: @escaping () -> Void) {
        self.isPro = isPro
        self.secondsLeft = secondsLeft
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if isPro {
                    Circle().fill(secondsLeft > 0 ? VocaTheme.green : VocaTheme.error).frame(width: 5, height: 5)
                    Text("\(VocaFormat.minutes(secondsLeft)) left")
                } else {
                    Text("Free · Upgrade")
                }
            }
            .vocaMicroLabel(9.5, color: isPro ? VocaTheme.violetDeep : VocaTheme.inkMute, em: 0.1)
            .padding(.horizontal, 11)
            .padding(.vertical, 6)
            .background(isPro ? VocaTheme.inkA07 : VocaTheme.surface)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(VocaTheme.hairline))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("tierChip")
    }
}

/// JSX `OfflineSwitch`: on the home screen above the language pair, with the
/// pair's on-device status and an install button when a download is needed.
public struct OfflineSwitch: View {
    @Binding var isOn: Bool
    let langA: String
    let langB: String
    let offline: OfflineAvailability

    @State private var status: PairStatus?
    @State private var busy = false
    @State private var error: String?

    public init(isOn: Binding<Bool>, langA: String, langB: String, offline: OfflineAvailability) {
        self._isOn = isOn
        self.langA = langA
        self.langB = langB
        self.offline = offline
    }

    private var pairPossible: Bool { Languages.canTranslateOffline(langA, langB) }
    private var needsDownload: Bool { isOn && pairPossible && status == .supported }
    private var impossible: Bool { isOn && !pairPossible }

    private var subtitle: String {
        let a = Languages.get(langA).label, b = Languages.get(langB).label
        if !pairPossible { return "\(a) and \(b) cannot be translated on-device — this pair needs a connection." }
        switch status {
        case .installed: return "Translated on this device. No connection needed, and nothing leaves your phone."
        case .supported: return "Both languages need to be installed on this device first."
        default: return "Runs on this device instead of the cloud."
        }
    }

    public var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 7) {
                        Text("Offline mode").vocaBody(14.5, weight: .semibold)
                        if isOn && status == .installed {
                            Text("Ready").vocaMicroLabel(9, color: VocaTheme.greenDeep, em: 0.1)
                        }
                    }
                    Text(subtitle).vocaBody(11.5, color: VocaTheme.inkMute).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                VocaSwitch(isOn: $isOn, label: "Offline mode")
            }
            if needsDownload {
                Button(action: download) {
                    Text(busy ? "Installing…" : "Install \(Languages.get(langA).label) and \(Languages.get(langB).label)")
                        .vocaBody(13.5, weight: .semibold, color: VocaTheme.violet)
                        .frame(maxWidth: .infinity)
                        .frame(height: 42)
                        .background(VocaTheme.inkA08)
                        .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
                        .opacity(busy ? 0.6 : 1)
                }
                .buttonStyle(.plain)
                .disabled(busy)
                .padding(.top, 11)
            }
            if let error {
                Text(error).vocaBody(11.5, color: VocaTheme.error)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 9)
            }
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 13)
        .background(VocaTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: VocaTheme.cardRadius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: VocaTheme.cardRadius, style: .continuous)
            .stroke(needsDownload || impossible ? VocaTheme.inkA35 : VocaTheme.hairline))
        .shadow(color: VocaTheme.ink.opacity(0.1), radius: 15, y: 10)
        .task(id: langA + "|" + langB) {
            error = nil
            status = await offline.pairStatus(langA, langB)
        }
    }

    private func download() {
        busy = true
        error = nil
        Task {
            do {
                let ok = try await offline.prepare(langA, langB)
                if !ok { error = "The download was declined. Offline translation needs both languages installed." }
                status = await offline.pairStatus(langA, langB)
            } catch {
                self.error = "Could not install the languages. Check your connection and try again."
            }
            busy = false
        }
    }
}

/// The canvas robot: a static illustration with a gentle hop (CONTRACT §7:
/// static is acceptable in v1; the hop is the bonus).
public struct VocaMascot: View {
    @State private var hop = false

    public init() {}

    public var body: some View {
        ZStack {
            Ellipse()
                .fill(RadialGradient(colors: [Color(hex: 0x4C3CB4).opacity(0.34), .clear], center: .center, startRadius: 0, endRadius: 44))
                .frame(width: 88, height: 14)
                .offset(y: 78)
                .scaleEffect(hop ? 0.85 : 1)

            ZStack {
                // Ears
                ForEach([-1.0, 1.0], id: \.self) { side in
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(LinearGradient(colors: [.white, Color(hex: 0xDED8F5)], startPoint: .top, endPoint: .bottom))
                        .frame(width: 22, height: 34)
                        .offset(x: side * 66, y: 8)
                }
                // Feet
                ForEach([-1.0, 1.0], id: \.self) { side in
                    UnevenRoundedRectangle(bottomLeadingRadius: 8, bottomTrailingRadius: 8)
                        .fill(LinearGradient(colors: [Color(hex: 0x2A2358), Color(hex: 0x151233)], startPoint: .top, endPoint: .bottom))
                        .frame(width: 22, height: 14)
                        .offset(x: side * 25, y: 64)
                }
                // Body
                RoundedRectangle(cornerRadius: 40, style: .continuous)
                    .fill(LinearGradient(colors: [.white, Color(hex: 0xF3F0FD), Color(hex: 0xDAD2F4), Color(hex: 0xC6BCEC)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                    .frame(width: 124, height: 122)
                    .shadow(color: Color(hex: 0x4C3CB4).opacity(0.5), radius: 17, y: 22)
                // Face
                RoundedRectangle(cornerRadius: 23, style: .continuous)
                    .fill(RadialGradient(colors: [Color(hex: 0x35286E), Color(hex: 0x1A1442), Color(hex: 0x0E0B29)],
                                         center: UnitPoint(x: 0.26, y: 0.14), startRadius: 0, endRadius: 110))
                    .frame(width: 82, height: 74)
                    .offset(y: -5)
                    .overlay(
                        VStack(spacing: 9) {
                            HStack(spacing: 22) {
                                ForEach(0..<2, id: \.self) { _ in
                                    RoundedRectangle(cornerRadius: 5).fill(.white).frame(width: 11, height: 15)
                                        .shadow(color: .white.opacity(0.75), radius: 5)
                                }
                            }
                            Circle().trim(from: 0.55, to: 0.95).stroke(.white, style: StrokeStyle(lineWidth: 3.5, lineCap: .round))
                                .frame(width: 30, height: 30).offset(y: -8)
                        }
                        .offset(y: -5)
                    )
                // Flag
                ZStack {
                    RoundedRectangle(cornerRadius: 2).fill(LinearGradient(colors: [Color(hex: 0xF3F0FD), Color(hex: 0xB9AEE8)], startPoint: .top, endPoint: .bottom))
                        .frame(width: 3.5, height: 70).offset(x: -18, y: 0)
                    UnevenRoundedRectangle(topLeadingRadius: 3, bottomLeadingRadius: 3, bottomTrailingRadius: 8, topTrailingRadius: 8)
                        .fill(LinearGradient(colors: [VocaTheme.violetLight, VocaTheme.violet, VocaTheme.violetDeep], startPoint: .topLeading, endPoint: .bottomTrailing))
                        .frame(width: 46, height: 30)
                        .overlay(Text("文A").font(VocaTheme.mono(14)).tracking(0.8).foregroundStyle(VocaTheme.ground))
                        .offset(x: 6, y: -22)
                }
                .offset(x: 82, y: -30)
            }
            .offset(y: hop ? -10 : 0)
        }
        .frame(width: 290, height: 180)
        .accessibilityHidden(true)
        .onAppear {
            withAnimation(.easeInOut(duration: 0.575).repeatForever(autoreverses: true)) { hop = true }
        }
    }
}
