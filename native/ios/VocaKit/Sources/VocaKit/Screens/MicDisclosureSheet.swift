import SwiftUI

/// JSX `MicDisclosure`: the prominent disclosure shown once before the OS
/// microphone prompt. Until it is accepted the app never asks for the mic.
public struct MicDisclosureSheet: View {
    let privacyURL: URL
    let onAccept: () -> Void
    let onCancel: () -> Void

    public init(privacyURL: URL, onAccept: @escaping () -> Void, onCancel: @escaping () -> Void) {
        self.privacyURL = privacyURL
        self.onAccept = onAccept
        self.onCancel = onCancel
    }

    private let lines = [
        "Audio is captured only while a speak button is held down — never in the background.",
        "Transcripts of the conversation are saved on this device so you can read them later.",
        "There are no accounts, and nothing is used for advertising or tracking.",
    ]

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Icon(.mic, size: 21, color: VocaTheme.ground)
                .frame(width: 46, height: 46)
                .background(VocaTheme.ink)
                .clipShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
            Text("Before we turn on the microphone").vocaDisplay(25)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 14)
            Text("To translate your conversation, Voca records audio while you hold the speak button and sends it to our servers, where it is transcribed and translated. Offline sessions never leave your iPhone.")
                .vocaBody(14, color: VocaTheme.ink.opacity(0.78))
                .lineSpacing(4)
                .padding(.top, 12)
            VStack(alignment: .leading, spacing: 9) {
                ForEach(lines, id: \.self) { line in
                    HStack(alignment: .top, spacing: 9) {
                        Text("·").vocaBody(13, weight: .bold, color: VocaTheme.violet)
                        Text(line).vocaBody(13, color: VocaTheme.ink.opacity(0.7)).lineSpacing(3)
                    }
                }
            }
            .padding(.top, 14)
            Link("Read the privacy policy", destination: privacyURL)
                .font(VocaTheme.body(13, weight: .semibold))
                .foregroundStyle(VocaTheme.violet)
                .underline()
                .padding(.top, 14)
            Button(action: onAccept) {
                Text("Allow and continue").vocaBody(16.5, weight: .semibold, color: .white)
                    .frame(maxWidth: .infinity).frame(height: 58)
                    .background(VocaTheme.violet)
                    .clipShape(RoundedRectangle(cornerRadius: 19, style: .continuous))
            }
            .buttonStyle(.plain)
            .padding(.top, 18)
            .accessibilityIdentifier("micDisclosureAccept")
            Button(action: onCancel) {
                Text("Not now").vocaBody(15, weight: .semibold, color: VocaTheme.ink.opacity(0.6))
                    .frame(maxWidth: .infinity).frame(height: 48)
            }
            .buttonStyle(.plain)
            .padding(.top, 8)
        }
        .padding(.horizontal, 22)
        .padding(.top, 24)
        .padding(.bottom, 28)
        .background(VocaTheme.ground)
        .presentationDetents([.large])
        .presentationDragIndicator(.hidden)
        .presentationBackground(VocaTheme.ground)
        .presentationCornerRadius(26)
    }
}
