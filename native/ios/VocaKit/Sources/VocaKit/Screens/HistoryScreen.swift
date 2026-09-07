import SwiftUI

/// JSX `HistoryScreen`: live sessions first (dark cards), then past sessions.
public struct HistoryScreen: View {
    let repository: HistoryRepository
    let onOpen: (String) -> Void

    @State private var sessions: [SessionSummary]?

    public init(repository: HistoryRepository, onOpen: @escaping (String) -> Void) {
        self.repository = repository
        self.onOpen = onOpen
    }

    public var body: some View {
        VStack(spacing: 0) {
            ScreenTitle("History")
            ScrollView {
                if let sessions {
                    if sessions.isEmpty {
                        VStack(spacing: 12) {
                            Icon(.history, size: 40, color: VocaTheme.border)
                            Text("No sessions yet").vocaBody(15, color: VocaTheme.textFaint)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 60)
                    } else {
                        let live = sessions.filter(\.isLive)
                        let ended = sessions.filter { !$0.isLive }
                        LazyVStack(alignment: .leading, spacing: 8) {
                            if !live.isEmpty {
                                Text("In progress").vocaMicroLabel(10.5, color: VocaTheme.greenDeep, em: 0.16).padding(.top, 6)
                                ForEach(live) { s in liveCard(s) }
                            }
                            if !ended.isEmpty {
                                Text("Past").vocaMicroLabel(10.5, color: VocaTheme.inkMute, em: 0.16).padding(.top, live.isEmpty ? 6 : 18)
                                ForEach(ended) { s in SessionCard(session: s) { onOpen(s.sessionId) } }
                            }
                        }
                        .padding(.horizontal, 20)
                        .padding(.bottom, 96)
                    }
                } else {
                    TypingDots().frame(maxWidth: .infinity).padding(.vertical, 40)
                }
            }
            .refreshable { await reload() }
        }
        .background(VocaTheme.ground.ignoresSafeArea())
        .task { await reload() }
        .task {
            // The JSX polled every 5s so a running session's clock keeps moving.
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                await reload()
            }
        }
    }

    private func reload() async {
        sessions = await repository.list()
    }

    private func liveCard(_ s: SessionSummary) -> some View {
        Button { onOpen(s.sessionId) } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Circle().fill(VocaTheme.green).frame(width: 7, height: 7)
                    Text("Live · \(VocaFormat.duration(s.duration))").vocaMicroLabel(10, color: VocaTheme.greenLight)
                }
                Text(s.topic ?? "Translation Session").vocaBody(17, weight: .semibold, color: VocaTheme.ground)
                Text(s.callerName ?? "").vocaBody(12, color: VocaTheme.ground.opacity(0.55))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(VocaTheme.ink)
            .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

/// JSX `SessionCard`.
public struct SessionCard: View {
    let session: SessionSummary
    let action: () -> Void

    public init(session: SessionSummary, action: @escaping () -> Void) {
        self.session = session
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Text((session.lang ?? "EN").uppercased())
                    .font(VocaTheme.body(11, weight: .bold))
                    .foregroundStyle(session.isLive ? VocaTheme.greenDeep : VocaTheme.violet)
                    .frame(width: 38, height: 38)
                    .background(session.isLive ? VocaTheme.green.opacity(0.12) : VocaTheme.violet.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(session.topic ?? session.callerName ?? "Translation Session")
                            .vocaBody(14.5, weight: .semibold).lineLimit(1)
                        Spacer(minLength: 0)
                        if session.isLive {
                            Text("Live").vocaMicroLabel(10, color: VocaTheme.greenDeep, em: 0.1)
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(VocaTheme.green.opacity(0.12)).clipShape(Capsule())
                        }
                    }
                    HStack(spacing: 8) {
                        Text(session.callerName ?? "").vocaBody(11.5, color: VocaTheme.ink.opacity(0.5))
                        if let n = session.participantCount { Text("· \(n) people").vocaBody(12, color: VocaTheme.textFaint) }
                        if let d = session.duration { Text("· \(VocaFormat.duration(d))").vocaBody(12, color: VocaTheme.textFaint) }
                    }
                }
            }
            .padding(.horizontal, 15)
            .padding(.vertical, 14)
            .background(VocaTheme.surface)
            .clipShape(RoundedRectangle(cornerRadius: VocaTheme.cardRadius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: VocaTheme.cardRadius, style: .continuous).stroke(VocaTheme.hairline))
        }
        .buttonStyle(.plain)
    }
}
