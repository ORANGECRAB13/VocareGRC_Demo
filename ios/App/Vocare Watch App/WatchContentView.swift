import SwiftUI

struct WatchContentView: View {
    @EnvironmentObject private var model: WatchTranslationModel
    @FocusState private var crownFocused: Bool
    @State private var crownPosition = 0.0
    @State private var lastCrownDetent = 0

    var body: some View {
        Group {
            switch model.phase {
            case .ready: readyView
            case .recording: recordingView
            case .translating: translatingView
            case .output: outputView
            case .session: sessionView
            case .error: errorView
            }
        }
        .background(ground)
        .focusable()
        .focused($crownFocused)
        // A wide non-wrapping range with unit detents: each full detent of
        // rotation fires exactly one swap. The old 0...1 wrapping range never
        // emitted reliable change events on real hardware.
        .digitalCrownRotation(
            $crownPosition, from: -1_000, through: 1_000, by: 1,
            sensitivity: .medium, isContinuous: false, isHapticFeedbackEnabled: false
        )
        .onChange(of: crownPosition) { value in
            let detent = Int(value.rounded())
            guard detent != lastCrownDetent else { return }
            lastCrownDetent = detent
            model.swapLanguages()
        }
        .onAppear { crownFocused = true }
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in model.beginHold() }
                .onEnded { _ in model.releaseHold() },
            isEnabled: model.phase == .ready || model.phase == .recording
        )
    }

    /// The design gives the two live screens their own radial wash over black;
    /// every other screen is flat black.
    @ViewBuilder private var ground: some View {
        switch model.phase {
        case .recording: WatchTheme.recordingGround
        case .output: WatchTheme.outputGround
        default: WatchTheme.ink.ignoresSafeArea()
        }
    }

    // MARK: - Raise to start

    private var readyView: some View {
        VStack(alignment: .leading, spacing: 0) {
            WatchBrandHeader()
            Text("Ready")
                .font(.system(size: 34, weight: .regular, design: .serif))
                .tracking(-0.34)
                .foregroundStyle(WatchTheme.cream)
                .minimumScaleFactor(0.8)
                .padding(.top, 6)
            HStack(spacing: 7) {
                Text(model.sourceLabel)
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundStyle(WatchTheme.orange)
                Text("→")
                    .font(.system(size: 12, weight: .regular, design: .rounded))
                    .foregroundStyle(WatchTheme.creamFaint)
                Text(model.targetLabel)
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundStyle(WatchTheme.teal)
            }
            .padding(.top, 10)
            Spacer(minLength: 6)
            HStack(spacing: 9) {
                Image(systemName: "mic.fill")
                    .font(.system(size: 15, weight: .semibold))
                Text("Hold to talk")
                    .font(.system(size: 16, weight: .bold, design: .rounded))
            }
            .foregroundStyle(WatchTheme.onOrange)
            .frame(maxWidth: .infinity, minHeight: 58)
            .background(WatchTheme.orange, in: RoundedRectangle(cornerRadius: 22))
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("Press and hold anywhere on screen to record")
            Text("Turn crown to swap languages")
                .font(.system(size: 10, weight: .regular, design: .rounded))
                .tracking(0.5)
                .foregroundStyle(WatchTheme.creamHint)
                .frame(maxWidth: .infinity)
                .padding(.top, 9)
        }
        .padding(.horizontal, 14)
        .padding(.bottom, 4)
        .contentShape(Rectangle())
    }

    // MARK: - Hold to speak

    private var recordingView: some View {
        VStack(alignment: .leading, spacing: 0) {
            WatchBrandHeader()
            KickerLabel(text: "Listening · \(model.sourceLabel)")
                .padding(.top, 6)
            // The app streams nothing until release, so this line is guidance
            // rather than the live partial transcript drawn in the design.
            Text("Keep speaking — pauses are held until release.")
                .font(.system(size: 19, weight: .medium, design: .rounded))
                .lineSpacing(2)
                .foregroundStyle(WatchTheme.cream)
                .lineLimit(3)
                .minimumScaleFactor(0.72)
                .padding(.top, 10)
            Spacer(minLength: 6)
            AudioBars()
                .frame(maxWidth: .infinity)
                .padding(.bottom, 14)
            Text("Release to send")
                .font(.system(size: 14.5, weight: .bold, design: .rounded))
                .foregroundStyle(WatchTheme.orange)
                .frame(maxWidth: .infinity, minHeight: 56)
                .background(WatchTheme.orangeWash, in: RoundedRectangle(cornerRadius: 22))
                .overlay(RoundedRectangle(cornerRadius: 22).stroke(WatchTheme.orange, lineWidth: 2))
        }
        .padding(.horizontal, 14)
        .padding(.bottom, 4)
        .contentShape(Rectangle())
    }

    // MARK: - Translating (no artboard; built from the same tokens)

    private var translatingView: some View {
        VStack(spacing: 0) {
            WatchBrandHeader()
            Spacer()
            ProgressView().tint(WatchTheme.orange).controlSize(.large)
            Text("Translating")
                .font(.system(size: 18, weight: .semibold, design: .rounded))
                .foregroundStyle(WatchTheme.cream)
                .padding(.top, 12)
            Text("Your recording is sent only after release.")
                .font(.system(size: 10, weight: .regular, design: .rounded))
                .foregroundStyle(WatchTheme.creamHint)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
            Spacer()
        }
        .padding(.horizontal, 14)
    }

    // MARK: - Translation out

    private var outputView: some View {
        VStack(alignment: .leading, spacing: 0) {
            WatchBrandHeader()
            HStack(spacing: 6) {
                PulsingDot(color: WatchTheme.teal)
                KickerLabel(text: "Speaking · \(model.targetLabel)", color: WatchTheme.teal)
                Spacer(minLength: 4)
                Button { model.showSession() } label: {
                    Text("\(model.turns.count) turns".uppercased())
                        .font(.system(size: 10, weight: .bold, design: .rounded))
                        .tracking(1.2)
                        .foregroundStyle(WatchTheme.creamMuted)
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 6)
            Text(model.latestTurn?.translated ?? "")
                .font(.system(size: 20, weight: .medium, design: .rounded))
                .lineSpacing(2)
                .foregroundStyle(WatchTheme.cream)
                .lineLimit(3)
                .minimumScaleFactor(0.62)
                .padding(.top, 10)
            Text(model.latestTurn?.original ?? "")
                .font(.system(size: 12.5, weight: .regular, design: .rounded).italic())
                .foregroundStyle(WatchTheme.secondary)
                .lineLimit(2)
                .padding(.top, 9)
            Spacer(minLength: 6)
            HStack(spacing: 8) {
                Button("Repeat") { try? model.playLastTranslation() }
                    .buttonStyle(SecondaryWatchButtonStyle())
                Button { model.reply() } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 12, weight: .semibold))
                        Text("Reply")
                    }
                }
                .buttonStyle(PrimaryWatchButtonStyle())
            }
        }
        .padding(.horizontal, 14)
        .padding(.bottom, 4)
    }

    // MARK: - Session

    private var sessionView: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Button(action: model.dismissSession) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(WatchTheme.creamMuted)
                }
                .buttonStyle(.plain)
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    Text(duration(model.elapsed))
                        .font(.system(size: 26, weight: .regular, design: .serif))
                        .monospacedDigit()
                        .foregroundStyle(WatchTheme.cream)
                }
                Text("\(model.turns.count) turns".uppercased())
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .tracking(1.2)
                    .foregroundStyle(WatchTheme.creamMuted)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 4)
            ScrollView {
                LazyVStack(spacing: 5) {
                    ForEach(model.turns) { turn in
                        TurnCard(turn: turn)
                    }
                }
            }
            .padding(.top, 8)
            Button(action: model.endSession) {
                HStack(spacing: 8) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(WatchTheme.orange)
                        .frame(width: 9, height: 9)
                    Text("End session")
                        .font(.system(size: 13.5, weight: .bold, design: .rounded))
                }
                .foregroundStyle(WatchTheme.orange)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(WatchTheme.orangeWash, in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(WatchTheme.orangeHairline, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .padding(.top, 8)
        }
        .padding(.horizontal, 10)
        .padding(.bottom, 4)
    }

    // MARK: - Error (no artboard; keeps the `stage` breadcrumb visible)

    private var errorView: some View {
        VStack(spacing: 0) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 18))
                .foregroundStyle(WatchTheme.orange)
            Text(model.errorMessage)
                .font(.system(size: 12, weight: .regular, design: .rounded))
                .foregroundStyle(WatchTheme.creamBody)
                .multilineTextAlignment(.center)
                .padding(.top, 10)
            Button("Try again") { model.retry() }
                .buttonStyle(PrimaryWatchButtonStyle())
                .padding(.top, 12)
        }
        .padding(14)
    }

    private func duration(_ interval: TimeInterval) -> String {
        String(format: "%02d:%02d", Int(interval) / 60, Int(interval) % 60)
    }
}

/// Session turn card: tinted ground, 2pt speaker-coloured rule down the leading
/// edge, uppercase speaker eyebrow over the spoken line.
private struct TurnCard: View {
    let turn: WatchConversationTurn

    private var isYou: Bool { turn.speaker == "You" }
    private var accent: Color { isYou ? WatchTheme.orange : WatchTheme.teal }
    private var ground: Color { isYou ? WatchTheme.orangeCard : WatchTheme.tealCard }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(turn.speaker.uppercased())
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .tracking(1.3)
                .foregroundStyle(accent)
            Text(turn.original)
                .font(.system(size: 12, weight: .regular, design: .rounded))
                .lineSpacing(1)
                .foregroundStyle(WatchTheme.creamBody)
            Text(turn.translated)
                .font(.system(size: 10, weight: .regular, design: .rounded))
                .foregroundStyle(WatchTheme.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 7)
        .padding(.leading, 9)
        .padding(.trailing, 9)
        .background(alignment: .leading) {
            ZStack(alignment: .leading) {
                ground
                accent.frame(width: 2)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

/// 11 orange bars scaling on their own staggered loops, per the design's
/// `wbar` keyframes (scaleY .2 → 1).
private struct AudioBars: View {
    @State private var animated = false

    var body: some View {
        HStack(alignment: .center, spacing: 3) {
            ForEach(0..<11, id: \.self) { index in
                RoundedRectangle(cornerRadius: 2)
                    .fill(WatchTheme.orange)
                    .opacity(0.5 + Double((index * 37) % 5) / 9)
                    .frame(width: 3, height: 26)
                    .scaleEffect(y: animated ? 1.0 : 0.2, anchor: .center)
                    .animation(
                        .easeInOut(duration: (620 + Double((index * 53) % 420)) / 1000)
                            .repeatForever()
                            .delay(Double(index) * 0.07),
                        value: animated
                    )
            }
        }
        .frame(height: 26)
        .onAppear { animated = true }
    }
}

/// The 6pt teal dot beside "Speaking", matching the design's `wdot` fade.
private struct PulsingDot: View {
    let color: Color
    @State private var on = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 6, height: 6)
            .opacity(on ? 1 : 0.25)
            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: on)
            .onAppear { on = true }
    }
}

private struct PrimaryWatchButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13.5, weight: .bold, design: .rounded))
            .foregroundStyle(WatchTheme.onOrange)
            .frame(maxWidth: .infinity, minHeight: 46)
            .background(
                WatchTheme.orange.opacity(configuration.isPressed ? 0.72 : 1),
                in: RoundedRectangle(cornerRadius: 18)
            )
    }
}

private struct SecondaryWatchButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13.5, weight: .semibold, design: .rounded))
            .foregroundStyle(WatchTheme.cream)
            .frame(maxWidth: .infinity, minHeight: 46)
            .background(
                Color(hex: 0xFBF3EB, opacity: configuration.isPressed ? 0.18 : 0.10),
                in: RoundedRectangle(cornerRadius: 18)
            )
    }
}
