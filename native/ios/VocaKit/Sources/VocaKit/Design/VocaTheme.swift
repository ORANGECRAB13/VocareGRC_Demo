import SwiftUI
import CoreText

// MARK: - Design tokens (CONTRACT §7, mirrors the JSX `T` table)

public enum VocaTheme {
    // Ink / grounds
    public static let ink = Color(hex: 0x1C2033)
    public static let ground = Color(hex: 0xFBF9F7)
    public static let surface = Color(hex: 0xFFFFFF)
    public static let surface2 = Color(hex: 0xF1EEFC)

    // Person A / brand violet
    public static let violet = Color(hex: 0x6C5CE7)
    public static let violetDeep = Color(hex: 0x5546C9)
    public static let violetLight = Color(hex: 0x8B7CF0)
    /// JSX `amberLight` — A's pale tint.
    public static let violetPale = Color(hex: 0xEBE7FD)

    // Person B / green
    public static let green = Color(hex: 0x34A46F)
    public static let greenDeep = Color(hex: 0x2A8B5D)
    public static let greenLight = Color(hex: 0x5FBF8E)
    /// JSX `tealLight` — B's pale tint.
    public static let greenPale = Color(hex: 0xE2F3E9)

    public static let pink = Color(hex: 0xE0397F)
    public static let error = Color(hex: 0xD93A5C)
    public static let errorLight = Color(hex: 0xFBE4E9)

    public static let textMuted = Color(hex: 0x6E7285)
    public static let textFaint = Color(hex: 0x9EA1AF)
    public static let border = Color(hex: 0xE6E4EC)

    // Live panels
    public static let panelAIdle = Color(hex: 0xF7F5FF)
    public static let panelAActive = Color(hex: 0xEBE7FD)
    public static let panelBIdle = Color(hex: 0xF4FAF6)
    public static let panelBActive = Color(hex: 0xE2F3E9)

    // Alpha washes lifted from the canvas
    public static let inkA07 = violet.opacity(0.07)
    public static let inkA08 = violet.opacity(0.08)
    public static let inkA14 = violet.opacity(0.14)
    public static let inkA35 = violet.opacity(0.35)
    public static let inkB09 = green.opacity(0.09)
    public static let inkB14 = green.opacity(0.14)
    public static let inkB35 = green.opacity(0.35)
    public static let tileA = violetLight.opacity(0.18)
    public static let tileB = green.opacity(0.16)
    public static let hairline = ink.opacity(0.08)
    public static let inkMute = ink.opacity(0.45)
    public static let inkFaint = ink.opacity(0.4)
    public static let inkSoft = ink.opacity(0.6)

    // Radii
    public static let cardRadius: CGFloat = 18
    public static let cardRadiusLarge: CGFloat = 22
    public static let buttonRadius: CGFloat = 20
    public static let micDiameter: CGFloat = 104

    // MARK: Type

    public enum Face: String, CaseIterable, Sendable {
        case outfitBold = "Outfit-Bold"
        case outfitMedium = "Outfit-Medium"
        case groteskRegular = "SpaceGrotesk-Regular"
        case groteskMedium = "SpaceGrotesk-Medium"
        case groteskSemiBold = "SpaceGrotesk-SemiBold"
        case groteskBold = "SpaceGrotesk-Bold"
        case monoMedium = "JetBrainsMono-Medium"
        case monoBold = "JetBrainsMono-Bold"
    }

    /// Outfit — display. Falls back to the system rounded face if the bundled
    /// font did not register (the name is still passed; SwiftUI substitutes).
    public static func display(_ size: CGFloat, weight: Font.Weight = .bold) -> Font {
        registerFontsOnce()
        let face: Face = weight == .bold || weight == .heavy || weight == .semibold ? .outfitBold : .outfitMedium
        return fontsAvailable ? .custom(face.rawValue, size: size) : .system(size: size, weight: weight, design: .rounded)
    }

    /// Space Grotesk — body.
    public static func body(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        registerFontsOnce()
        let face: Face
        switch weight {
        case .bold, .heavy, .black: face = .groteskBold
        case .semibold: face = .groteskSemiBold
        case .medium: face = .groteskMedium
        default: face = .groteskRegular
        }
        return fontsAvailable ? .custom(face.rawValue, size: size) : .system(size: size, weight: weight)
    }

    /// JetBrains Mono — uppercase micro-labels. Pair with `.tracking(...)`.
    public static func mono(_ size: CGFloat, weight: Font.Weight = .bold) -> Font {
        registerFontsOnce()
        let face: Face = weight == .bold || weight == .heavy ? .monoBold : .monoMedium
        return fontsAvailable ? .custom(face.rawValue, size: size) : .system(size: size, weight: weight, design: .monospaced)
    }

    /// Letter-spacing for micro-labels: .12–.16em.
    public static func microTracking(_ size: CGFloat, em: CGFloat = 0.14) -> CGFloat { size * em }
    /// Display tracking: −0.02em.
    public static func displayTracking(_ size: CGFloat) -> CGFloat { -0.02 * size }

    // MARK: Font registration

    nonisolated(unsafe) private static var fontsAvailable = false
    private static let registration: Void = {
        var registered = 0
        for face in Face.allCases {
            guard let url = Bundle.module.url(forResource: face.rawValue, withExtension: "ttf", subdirectory: nil)
                ?? Bundle.module.url(forResource: face.rawValue, withExtension: "ttf", subdirectory: "Fonts") else { continue }
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) {
                registered += 1
            } else if let error = error?.takeRetainedValue(), CFErrorGetCode(error) == CTFontManagerError.alreadyRegistered.rawValue {
                registered += 1
            }
        }
        fontsAvailable = registered == Face.allCases.count
    }()

    /// Idempotent; safe to call from any view body. Public so the app can warm it at launch.
    public static func registerFontsOnce() { _ = registration }
}

// MARK: - Color helpers

public extension Color {
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

// MARK: - Text style modifiers

public extension View {
    /// Uppercase JetBrains Mono micro-label with the canvas tracking.
    func vocaMicroLabel(_ size: CGFloat = 10, color: Color = VocaTheme.inkMute, em: CGFloat = 0.14) -> some View {
        self.font(VocaTheme.mono(size)).tracking(VocaTheme.microTracking(size, em: em)).textCase(.uppercase).foregroundStyle(color)
    }

    func vocaDisplay(_ size: CGFloat, color: Color = VocaTheme.ink) -> some View {
        self.font(VocaTheme.display(size)).tracking(VocaTheme.displayTracking(size)).foregroundStyle(color)
    }

    func vocaBody(_ size: CGFloat, weight: Font.Weight = .regular, color: Color = VocaTheme.ink) -> some View {
        self.font(VocaTheme.body(size, weight: weight)).foregroundStyle(color)
    }
}
