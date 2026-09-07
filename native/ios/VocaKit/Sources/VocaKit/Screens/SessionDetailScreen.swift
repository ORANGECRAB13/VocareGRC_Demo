import SwiftUI

/// JSX `SessionDetailScreen`: dark header, notes card, full transcript.
public struct SessionDetailScreen: View {
    let sessionId: String
    let repository: HistoryRepository
    let onBack: () -> Void

    @State private var detail: SessionDetail?

    public init(sessionId: String, repository: HistoryRepository, onBack: @escaping () -> Void) {
        self.sessionId = sessionId
        self.repository = repository
        self.onBack = onBack
    }

    public var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                BackButton(light: true, action: onBack)
                Text(detail?.topic ?? "Translation session")
                    .vocaDisplay(27, color: VocaTheme.ground)
                    .padding(.top, 14)
                Text(detail?.callerName ?? sessionId)
                    .vocaBody(12, color: VocaTheme.ground.opacity(0.55))
                    .padding(.top, 6)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.top, 12)
            .padding(.bottom, 16)
            .background(VocaTheme.ink)
            .clipShape(UnevenRoundedRectangle(bottomLeadingRadius: 28, bottomTrailingRadius: 28, style: .continuous))
            .ignoresSafeArea(edges: .top)

            ScrollView {
                let transcript = detail?.transcript ?? []
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 11) {
                        Text("Automated notes").vocaMicroLabel(10.5, color: VocaTheme.violet, em: 0.16)
                        Text(transcript.isEmpty
                             ? "Notes will appear after translated conversation turns are recorded."
                             : "\(transcript.count) translated conversation turn\(transcript.count == 1 ? "" : "s") recorded.")
                            .vocaBody(13.5).lineSpacing(3)
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(VocaTheme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(VocaTheme.violet.opacity(0.18)))

                    Text("Full transcript").vocaMicroLabel(10.5, color: VocaTheme.inkMute, em: 0.16)
                        .padding(.top, 20).padding(.bottom, 10)

                    VStack(spacing: 12) {
                        ForEach(Array(transcript.enumerated()), id: \.offset) { i, turn in
                            let isB = isSideB(turn, index: i)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(turn.speakerName ?? (isB ? "Person B" : "Person A"))
                                    .vocaMicroLabel(9.5, color: isB ? VocaTheme.greenDeep : VocaTheme.violet, em: 0.12)
                                Text(turn.original).vocaBody(14.5).lineSpacing(3)
                                    .padding(.horizontal, 13).padding(.vertical, 11)
                                    .background(isB ? VocaTheme.surface : VocaTheme.violet.opacity(0.06))
                                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                                    .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                                        .stroke(isB ? VocaTheme.green.opacity(0.2) : VocaTheme.violet.opacity(0.16)))
                                Text(turn.translated).vocaBody(11.5, color: VocaTheme.ink.opacity(0.42)).italic()
                            }
                            .frame(maxWidth: .infinity, alignment: isB ? .trailing : .leading)
                            .frame(maxWidth: .infinity)
                            .padding(isB ? .leading : .trailing, 40)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 40)
            }
        }
        .background(VocaTheme.ground.ignoresSafeArea())
        .task(id: sessionId) { detail = await repository.detail(sessionId) }
    }

    /// Attribute by language when we can (A's language is `lang`), else alternate like the JSX.
    private func isSideB(_ turn: TranscriptTurn, index: Int) -> Bool {
        if let lang = detail?.lang, let original = turn.originalLang { return original != lang }
        return index % 2 == 1
    }
}
