import SwiftUI

/// JSX `PaywallScreen`: three variants (`upsell` / `unsupported` / `exhausted`),
/// the storefront's localized price, restore.
public struct PaywallScreen: View {
    @ObservedObject var entitlements: EntitlementStore
    let reason: EntitlementStore.PaywallReason
    let langA: String
    let langB: String
    let onClose: () -> Void
    let onPurchased: (Balance) -> Void

    @State private var busy: Busy?
    @State private var error: String?

    private enum Busy { case purchase, restore }

    public init(entitlements: EntitlementStore, reason: EntitlementStore.PaywallReason, langA: String, langB: String,
                onClose: @escaping () -> Void, onPurchased: @escaping (Balance) -> Void) {
        self.entitlements = entitlements
        self.reason = reason
        self.langA = langA
        self.langB = langB
        self.onClose = onClose
        self.onPurchased = onPurchased
    }

    private var heading: String {
        switch reason {
        case .exhausted: return "You are out of minutes"
        case .unsupported: return "This pair needs Voca Pro"
        case .upsell: return "Voca Pro"
        }
    }

    private var lede: String {
        switch reason {
        case .exhausted:
            return "Your \(PurchasesService.planMinutes) minutes reset at the start of next month. Until then you can keep translating on-device, free."
        case .unsupported:
            return "\(Languages.get(langA).label) to \(Languages.get(langB).label) has no on-device model, so it needs the cloud engine."
        case .upsell:
            return "Natural two-way conversation, translated live by a voice on each side."
        }
    }

    private let perks: [(Icon.Name, String, String)] = [
        (.mic, "Two live voices", "Each person hears the other in their own language, in the moment."),
        (.globe, "Every language pair", "All languages, including the pairs no phone can do on-device."),
        (.clock, "\(PurchasesService.planMinutes) minutes a month", "Minutes count real conversation, not time the app sits idle."),
    ]

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                BackButton(action: onClose)
                    .padding(.horizontal, 22)
                    .padding(.top, 12)
                    .accessibilityLabel("Close")

                VStack(alignment: .leading, spacing: 8) {
                    Text(reason == .exhausted ? "Monthly allowance" : "Upgrade")
                        .vocaMicroLabel(10, color: VocaTheme.violet, em: 0.16)
                    Text(heading).font(VocaTheme.display(33)).tracking(-0.8).foregroundStyle(VocaTheme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(lede).vocaBody(14, color: VocaTheme.inkMute).lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 2)
                }
                .padding(.horizontal, 22)
                .padding(.top, 18)

                if let balance = entitlements.balance, balance.isPro {
                    VocaCard(padding: 0) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Remaining this month").vocaMicroLabel(9.5, color: VocaTheme.inkMute)
                            Text(VocaFormat.minutes(balance.secondsRemaining)).vocaDisplay(26)
                            ProgressBar(fraction: balance.secondsTotal > 0
                                        ? Double(balance.secondsRemaining) / Double(balance.secondsTotal) : 0)
                                .padding(.top, 6)
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 18)
                }

                VStack(spacing: 8) {
                    ForEach(perks, id: \.1) { perk in
                        VocaCard {
                            HStack(alignment: .top, spacing: 12) {
                                Icon(perk.0, size: 18, color: VocaTheme.violet)
                                    .frame(width: 38, height: 38)
                                    .background(VocaTheme.inkA08)
                                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(perk.1).vocaBody(14.5, weight: .semibold)
                                    Text(perk.2).vocaBody(12, color: VocaTheme.inkMute).lineSpacing(3)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, 15)
                            .padding(.vertical, 14)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 18)

                VStack(spacing: 6) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(entitlements.displayPrice).font(VocaTheme.display(34)).tracking(-0.85).foregroundStyle(VocaTheme.ink)
                            .accessibilityIdentifier("paywallPrice")
                        Text("/ month").vocaBody(14, color: VocaTheme.inkMute)
                    }
                    Text("Renews monthly. Cancel any time in your App Store account.")
                        .vocaBody(11.5, color: VocaTheme.textFaint)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 20)

                VStack(spacing: 8) {
                    if let error {
                        Text(error).vocaBody(12.5, color: VocaTheme.error).lineSpacing(3)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 13).padding(.vertical, 11)
                            .background(VocaTheme.errorLight)
                            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                            .padding(.bottom, 2)
                    }
                    PrimaryButton(busy == .purchase ? "Contacting the store…" : "Subscribe — \(entitlements.displayPrice)/mo",
                                  busy: busy != nil) { run(.purchase) }
                        .accessibilityIdentifier("paywallSubscribe")
                    Button { run(.restore) } label: {
                        Text(busy == .restore ? "Checking…" : "Restore a previous purchase")
                            .vocaBody(13.5, weight: .semibold, color: VocaTheme.violet)
                            .frame(maxWidth: .infinity).frame(height: 46)
                    }
                    .buttonStyle(.plain)
                    .disabled(busy != nil)
                }
                .padding(.horizontal, 20)
                .padding(.top, 16)

                Button(action: onClose) {
                    Text(reason == .unsupported ? "Pick another language pair" : "Keep translating on-device, free")
                        .vocaBody(13, weight: .semibold, color: VocaTheme.inkMute)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 24)
                .padding(.top, 10)
                .padding(.bottom, 32)
            }
        }
        .background(VocaTheme.ground.ignoresSafeArea())
    }

    private func run(_ kind: Busy) {
        busy = kind
        error = nil
        Task {
            let result = kind == .purchase ? await entitlements.purchase() : await entitlements.restore()
            switch result {
            case .activated(let balance):
                onPurchased(balance)
            case .cancelled:
                break
            case .pending:
                error = "Your purchase is awaiting approval. Minutes appear as soon as it clears."
            case .notFound:
                error = "No previous subscription found on this account."
            case .unverified:
                error = "Your purchase could not be verified yet. Please try Restore purchases when connected, or contact support."
            case .failed:
                error = "The purchase could not be completed or confirmed. Please try Restore purchases before buying again."
            }
            busy = nil
        }
    }
}

public struct ProgressBar: View {
    let fraction: Double
    public init(fraction: Double) { self.fraction = fraction }
    public var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(VocaTheme.ink.opacity(0.08))
                Capsule().fill(VocaTheme.violet).frame(width: geo.size.width * min(1, max(0, fraction)))
            }
        }
        .frame(height: 6)
    }
}
