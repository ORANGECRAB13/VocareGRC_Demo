import SwiftUI

/// JSX `ErrorScreen`: mic denied, or the connection failed.
public struct ErrorScreen: View {
    let kind: ErrorKind
    let onRetry: () -> Void
    let onBack: () -> Void

    public init(kind: ErrorKind, onRetry: @escaping () -> Void, onBack: @escaping () -> Void) {
        self.kind = kind
        self.onRetry = onRetry
        self.onBack = onBack
    }

    private var isMic: Bool { kind == .mic }

    public var body: some View {
        VStack(spacing: 0) {
            Spacer()
            Icon(isMic ? .micOff : .wifiOff, size: 36, color: VocaTheme.error)
                .frame(width: 80, height: 80)
                .background(VocaTheme.errorLight)
                .clipShape(Circle())
                .padding(.bottom, 20)
            Text(isMic ? "Microphone access required" : "Connection failed")
                .vocaDisplay(28)
                .multilineTextAlignment(.center)
                .padding(.bottom, 8)
            Text(isMic
                 ? "Voca needs microphone access to translate speech. Allow it for Voca in your device settings, then try again."
                 : "Unable to connect to the translation server. Please check your connection and try again.")
                .vocaBody(13.5, color: VocaTheme.inkMute)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .frame(maxWidth: 280)
                .padding(.bottom, 32)
            VStack(spacing: 10) {
                if isMic, let url = URL(string: UIApplication.openSettingsURLString) {
                    Link(destination: url) {
                        Text("Open Settings").vocaBody(16, weight: .semibold, color: .white)
                            .frame(maxWidth: .infinity).frame(height: 56)
                            .background(VocaTheme.violet)
                            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                            .shadow(color: VocaTheme.violet.opacity(0.6), radius: 12, y: 12)
                    }
                    SecondaryButton("Try again", action: onRetry)
                } else {
                    Button(action: onRetry) {
                        Text("Try again").vocaBody(16, weight: .semibold, color: .white)
                            .frame(maxWidth: .infinity).frame(height: 56)
                            .background(VocaTheme.violet)
                            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                            .shadow(color: VocaTheme.violet.opacity(0.6), radius: 12, y: 12)
                    }
                    .buttonStyle(.plain)
                }
                SecondaryButton("Go back", action: onBack)
            }
            .frame(maxWidth: 280)
            Spacer()
        }
        .padding(.horizontal, 24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(VocaTheme.ground.ignoresSafeArea())
    }
}
