import SwiftUI

extension Color {
    /// Design tokens are authored as hex in the design file; this keeps the
    /// Swift side a literal transcription of them.
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}

enum WatchTheme {
    // MARK: Palette (exact hexes from "Voca Watch.dc.html")

    /// #F47C36 — primary accent, the wearer's own side.
    static let orange = Color(hex: 0xF47C36)
    /// #58BFB4 — the other side / translated audio.
    static let teal = Color(hex: 0x58BFB4)
    /// #FBF3EB — cream, all foreground text on the watch face.
    static let cream = Color(hex: 0xFBF3EB)
    /// #1B1206 — text sitting on top of orange fills.
    static let onOrange = Color(hex: 0x1B1206)
    /// The watch screen itself is pure black in the design.
    static let ink = Color.black

    // MARK: Derived surfaces (design opacities, kept as named tokens)

    /// rgba(244,124,54,.16) — filled orange chip / recording button ground.
    static let orangeWash = Color(hex: 0xF47C36, opacity: 0.16)
    /// rgba(244,124,54,.40) — hairline on the orange chip.
    static let orangeHairline = Color(hex: 0xF47C36, opacity: 0.40)
    /// rgba(244,124,54,.14) — "You" turn card ground.
    static let orangeCard = Color(hex: 0xF47C36, opacity: 0.14)
    /// rgba(88,191,180,.14) — other-speaker turn card ground.
    static let tealCard = Color(hex: 0x58BFB4, opacity: 0.14)
    /// rgba(251,243,235,.10) — secondary button ground.
    static let creamWash = Color(hex: 0xFBF3EB, opacity: 0.10)

    // MARK: Text ramp

    /// rgba(251,243,235,.86) — turn body copy.
    static let creamBody = Color(hex: 0xFBF3EB, opacity: 0.86)
    /// rgba(251,243,235,.75) — status-bar clock.
    static let creamStatus = Color(hex: 0xFBF3EB, opacity: 0.75)
    /// rgba(251,243,235,.42) — source line under a translation.
    static let secondary = Color(hex: 0xFBF3EB, opacity: 0.42)
    /// rgba(251,243,235,.40) — turn-count label.
    static let creamMuted = Color(hex: 0xFBF3EB, opacity: 0.40)
    /// rgba(251,243,235,.38) — crown hint.
    static let creamHint = Color(hex: 0xFBF3EB, opacity: 0.38)
    /// rgba(251,243,235,.35) — the arrow between language codes.
    static let creamFaint = Color(hex: 0xFBF3EB, opacity: 0.35)

    // MARK: Screen grounds

    /// radial-gradient(120% 80% at 50% 108%, rgba(244,124,54,.42) 0%, transparent 62%), #000
    static var recordingGround: some View {
        ZStack {
            ink
            EllipticalGradient(
                colors: [Color(hex: 0xF47C36, opacity: 0.42), Color(hex: 0xF47C36, opacity: 0)],
                center: UnitPoint(x: 0.5, y: 1.08),
                startRadiusFraction: 0,
                endRadiusFraction: 0.62
            )
        }
        .ignoresSafeArea()
    }

    /// radial-gradient(120% 80% at 50% -8%, rgba(88,191,180,.34) 0%, transparent 60%), #000
    static var outputGround: some View {
        ZStack {
            ink
            EllipticalGradient(
                colors: [Color(hex: 0x58BFB4, opacity: 0.34), Color(hex: 0x58BFB4, opacity: 0)],
                center: UnitPoint(x: 0.5, y: -0.08),
                startRadiusFraction: 0,
                endRadiusFraction: 0.60
            )
        }
        .ignoresSafeArea()
    }
}

/// Status row: the "Voca" wordmark in orange with the clock opposite it,
/// matching the 12px/600 status strip drawn at the top of every artboard.
struct WatchBrandHeader: View {
    var body: some View {
        HStack {
            Text("Voca")
                .font(.system(size: 12, weight: .semibold, design: .rounded))
                .foregroundStyle(WatchTheme.orange)
            Spacer()
            TimelineView(.everyMinute) { context in
                Text(context.date, style: .time)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(WatchTheme.creamStatus)
            }
        }
    }
}

/// 10px / 700 / .16em uppercase eyebrow used above the live text on the
/// recording and output screens.
struct KickerLabel: View {
    let text: String
    var color = WatchTheme.orange
    var size: CGFloat = 10
    var tracking: CGFloat = 1.6

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: size, weight: .bold, design: .rounded))
            .tracking(tracking)
            .foregroundStyle(color)
    }
}
