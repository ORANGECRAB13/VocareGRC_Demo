import SwiftUI
import VocaKit

// MARK: - LiveSplitView (CONTRACT §5/§6, mirrors JSX `LiveSplitView`)
//
// Presentation only. Both live screens render this, so a cloud session and an
// on-device session look and behave identically; only the centre-bar badge
// differs. Person B's half is rotated 180° so they read it from across the
// table; Person A (the phone's owner) has the bottom half.

public struct LiveSplitView: View {
    public let state: LiveState
    public let onHold: (Side) -> Void
    public let onRelease: (Side) -> Void
    public let onEnd: () -> Void
    public let onSwap: (() -> Void)?

    public init(state: LiveState,
                onHold: @escaping (Side) -> Void,
                onRelease: @escaping (Side) -> Void,
                onEnd: @escaping () -> Void,
                onSwap: (() -> Void)? = nil) {
        self.state = state
        self.onHold = onHold
        self.onRelease = onRelease
        self.onEnd = onEnd
        self.onSwap = onSwap
    }

    public var body: some View {
        VStack(spacing: 0) {
            LiveSidePanel(side: .b, data: state.sideB, rotated: true,
                          onHold: { onHold(.b) }, onRelease: { onRelease(.b) })

            centreBar

            if let notice = state.notice {
                Text(notice)
                    .vocaBody(12, color: VocaTheme.error)
                    .lineSpacing(3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 9)
                    .background(VocaTheme.errorLight)
                    .transition(.opacity)
            }

            LiveSidePanel(side: .a, data: state.sideA, rotated: false,
                          onHold: { onHold(.a) }, onRelease: { onRelease(.a) })
        }
        .background(VocaTheme.ground)
        .ignoresSafeArea(edges: .bottom)
        .animation(.easeOut(duration: 0.25), value: state.notice)
    }

    private var swapDisabled: Bool {
        onSwap == nil || state.sideA.pressing || state.sideB.pressing
            || state.sideA.dimmed || state.sideB.dimmed
    }

    private var centreBar: some View {
        HStack(spacing: 12) {
            Button {
                onSwap?()
            } label: {
                HStack(spacing: 7) {
                    Text(state.sideB.langCode.uppercased())
                        .vocaBody(11, weight: .bold, color: VocaTheme.greenDeep)
                    Icon(.swap, size: 15, color: VocaTheme.ink.opacity(0.35))
                    Text(state.sideA.langCode.uppercased())
                        .vocaBody(11, weight: .bold, color: VocaTheme.violetDeep)
                }
                .padding(.vertical, 8)
                .padding(.horizontal, 4)
            }
            .buttonStyle(.plain)
            .disabled(swapDisabled)
            .opacity(onSwap != nil && swapDisabled ? 0.45 : 1)
            .accessibilityLabel(onSwap != nil ? "Swap languages during session" : "Language direction")

            HStack(spacing: 8) {
                if let badge = state.badge {
                    Text(badge)
                        .vocaMicroLabel(9, color: VocaTheme.greenDeep, em: 0.12)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Capsule().fill(VocaTheme.greenPale))
                        .accessibilityLabel("On device")
                } else {
                    LiveDot()
                }
                Text(state.elapsedClock)
                    .vocaBody(12.5, weight: .medium, color: VocaTheme.ink.opacity(0.75))
                    .monospacedDigit()
            }
            .frame(maxWidth: .infinity)

            Button(action: onEnd) {
                HStack(spacing: 7) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(VocaTheme.greenDeep)
                        .frame(width: 9, height: 9)
                    Text("End")
                        .vocaBody(12.5, weight: .semibold, color: VocaTheme.greenDeep)
                }
                .padding(.horizontal, 14)
                .frame(height: 34)
                .background(RoundedRectangle(cornerRadius: 12).fill(VocaTheme.green.opacity(0.15)))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("End session")
        }
        .padding(.horizontal, 16)
        .frame(height: 56)
        .background(VocaTheme.surface)
        .overlay(alignment: .top) { VocaTheme.hairline.frame(height: 1) }
        .overlay(alignment: .bottom) { VocaTheme.hairline.frame(height: 1) }
    }
}

// MARK: - One half of the screen

struct LiveSidePanel: View {
    let side: Side
    let data: SideState
    let rotated: Bool
    let onHold: () -> Void
    let onRelease: () -> Void

    private var isA: Bool { side == .a }
    private var accent: Color { isA ? VocaTheme.violet : VocaTheme.green }
    private var accentDeep: Color { isA ? VocaTheme.violetDeep : VocaTheme.greenDeep }
    private var idleBg: Color { isA ? VocaTheme.panelAIdle : VocaTheme.panelBIdle }
    private var activeBg: Color { isA ? VocaTheme.panelAActive : VocaTheme.panelBActive }
    private var micIdle: Color { isA ? VocaTheme.inkA14 : VocaTheme.inkB14 }
    private var micBorder: Color { isA ? VocaTheme.inkA35 : VocaTheme.inkB35 }
    private var tile: Color { isA ? VocaTheme.tileA : VocaTheme.tileB }

    var body: some View {
        VStack(spacing: 0) {
            header
            turnList
            micArea
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(data.pressing ? activeBg : idleBg)
        .animation(.easeInOut(duration: 0.3), value: data.pressing)
        .rotationEffect(rotated ? .degrees(180) : .zero)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(data.name), \(data.langLabel)")
    }

    private var header: some View {
        HStack(spacing: 10) {
            PersonNameTag(code: data.langCode, label: data.langLabel, name: data.name,
                          tint: tile, ink: isA ? accent : accentDeep)
            Spacer(minLength: 0)
            Text(data.status)
                .vocaMicroLabel(10.5, color: data.pressing ? accentDeep : VocaTheme.inkFaint, em: 0.1)
                .lineLimit(1)
        }
        .padding(EdgeInsets(top: 14, leading: 18, bottom: 6, trailing: 18))
    }

    /// Newest turn sits nearest this person's microphone (JSX `column-reverse`).
    private var turnList: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(data.turns) { turn in
                    LiveTurn(turn: turn, side: side)
                        .frame(maxWidth: .infinity, alignment: turn.mine ? .trailing : .leading)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                if let note = data.note {
                    HStack(spacing: 8) {
                        TypingDots(color: VocaTheme.inkFaint)
                        Text(note).vocaBody(13, color: VocaTheme.inkMute)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(EdgeInsets(top: 6, leading: 18, bottom: 4, trailing: 18))
            .frame(maxWidth: .infinity)
        }
        .defaultScrollAnchor(.bottom)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.easeOut(duration: 0.3), value: data.turns.count)
    }

    private var micArea: some View {
        ZStack {
            if data.pressing {
                PulseRing(color: accent, size: VocaTheme.micDiameter)
            }
            MicButton(
                pressing: data.pressing,
                disabled: data.disabled,
                dimmed: data.dimmed,
                fill: data.pressing ? accent : micIdle,
                border: data.pressing ? accentDeep : micBorder,
                iconColor: data.pressing ? .white : (isA ? accent : accentDeep),
                label: "\(data.name) hold to speak",
                onHold: onHold,
                onRelease: onRelease
            )
        }
        .frame(width: VocaTheme.micDiameter, height: VocaTheme.micDiameter)
        .frame(maxWidth: .infinity)
        .frame(height: 150)
        .padding(.bottom, rotated ? 0 : bottomInset)
    }

    private var bottomInset: CGFloat {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        return scenes.flatMap(\.windows).first { $0.isKeyWindow }?.safeAreaInsets.bottom ?? 0
    }
}

// MARK: - Push-to-talk button (104pt circle)

struct MicButton: View {
    let pressing: Bool
    let disabled: Bool
    let dimmed: Bool
    let fill: Color
    let border: Color
    let iconColor: Color
    let label: String
    let onHold: () -> Void
    let onRelease: () -> Void

    @State private var tracking = false

    var body: some View {
        ZStack {
            Circle().fill(fill)
            Circle().strokeBorder(border, lineWidth: 2)
            Icon(.mic, size: 30, color: iconColor)
        }
        .frame(width: VocaTheme.micDiameter, height: VocaTheme.micDiameter)
        .scaleEffect(pressing ? 1.12 : 1)
        .opacity(dimmed ? 0.58 : 1)
        .animation(.easeOut(duration: 0.12), value: pressing)
        .contentShape(Circle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !disabled, !tracking else { return }
                    tracking = true
                    onHold()
                }
                .onEnded { _ in
                    guard tracking else { return }
                    tracking = false
                    onRelease()
                },
            including: disabled ? .subviews : .all
        )
        .onChange(of: disabled) { _, nowDisabled in
            // Lost the press while the button was disabled underneath us.
            if nowDisabled, tracking, !pressing {
                tracking = false
            }
        }
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Hold to speak, release to translate")
    }
}

// MARK: - Turn bubble

struct LiveTurn: View {
    let turn: TurnView
    let side: Side

    var body: some View {
        let mine = turn.mine
        let tagColor = mine ? VocaTheme.inkFaint : (side == .a ? VocaTheme.violetDeep : VocaTheme.greenDeep)
        let bg = mine ? VocaTheme.surface : (side == .a ? VocaTheme.violet : VocaTheme.green)
        let fg = mine ? VocaTheme.ink : Color.white

        VStack(alignment: mine ? .trailing : .leading, spacing: 4) {
            Text(mine ? "You said" : "Translated")
                .vocaMicroLabel(9.5, color: tagColor, em: 0.12)
            Text(mine ? turn.original : turn.translated)
                .vocaBody(15.5, color: fg)
                .lineSpacing(4)
                .multilineTextAlignment(.leading)
                .padding(EdgeInsets(top: 11, leading: 13, bottom: 11, trailing: 13))
                .background(RoundedRectangle(cornerRadius: 16).fill(bg))
                .fixedSize(horizontal: false, vertical: true)
            if !mine {
                Text(turn.original)
                    .vocaBody(11.5, color: VocaTheme.inkFaint)
                    .italic()
                    .padding(.top, 1)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: 320, alignment: mine ? .trailing : .leading)
    }
}

// MARK: - Name tag

struct PersonNameTag: View {
    let code: String
    let label: String
    let name: String
    let tint: Color
    let ink: Color

    var body: some View {
        HStack(spacing: 10) {
            Text(code.uppercased())
                .vocaBody(11, weight: .bold, color: ink)
                .frame(width: 30, height: 30)
                .background(RoundedRectangle(cornerRadius: 10).fill(tint))
            VStack(alignment: .leading, spacing: 1) {
                Text(name).vocaBody(14, weight: .semibold, color: VocaTheme.ink)
                Text(label).vocaBody(11, color: VocaTheme.inkMute)
            }
        }
    }
}

// MARK: - Motion

struct PulseRing: View {
    let color: Color
    let size: CGFloat

    @State private var animating = false

    var body: some View {
        ZStack {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .strokeBorder(color, lineWidth: 2)
                    .frame(width: size, height: size)
                    .scaleEffect(animating ? 1.7 : 1)
                    .opacity(animating ? 0 : 0.7)
                    .animation(
                        .easeOut(duration: 1.8).repeatForever(autoreverses: false).delay(Double(index) * 0.6),
                        value: animating
                    )
            }
        }
        .allowsHitTesting(false)
        .onAppear { animating = true }
    }
}

struct LiveDot: View {
    @State private var on = true

    var body: some View {
        Circle()
            .fill(VocaTheme.pink)
            .frame(width: 6, height: 6)
            .opacity(on ? 1 : 0.25)
            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: on)
            .onAppear { on = false }
            .accessibilityLabel("Live")
    }
}

struct TypingDots: View {
    let color: Color
    @State private var on = false

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(color)
                    .frame(width: 6, height: 6)
                    .opacity(on ? 1 : 0.3)
                    .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true).delay(Double(index) * 0.2), value: on)
            }
        }
        .onAppear { on = true }
    }
}

// MARK: - Preview

#Preview("Live split") {
    LiveSplitView(
        state: LiveState(
            sideA: SideState(name: "Alex", langCode: "en", langLabel: "English", pressing: false, disabled: false,
                             status: "Ready - speak again",
                             turns: [
                                TurnView(id: 0, original: "Hello, welcome to the clinic.", translated: "你好，欢迎来到诊所。", mine: true),
                                TurnView(id: 1, original: "你好，我今天早上预约了。", translated: "Hello, I have an appointment this morning.", mine: false),
                             ]),
            sideB: SideState(name: "Mei", langCode: "zh", langLabel: "Mandarin", pressing: true, disabled: false,
                             status: "Recording",
                             turns: [
                                TurnView(id: 0, original: "Hello, welcome to the clinic.", translated: "你好，欢迎来到诊所。", mine: false),
                                TurnView(id: 1, original: "你好，我今天早上预约了。", translated: "Hello, I have an appointment this morning.", mine: true),
                             ], note: "我叫李美"),
            elapsed: 83, phase: .live, badge: "On device", notice: nil
        ),
        onHold: { _ in }, onRelease: { _ in }, onEnd: {}, onSwap: {}
    )
}
