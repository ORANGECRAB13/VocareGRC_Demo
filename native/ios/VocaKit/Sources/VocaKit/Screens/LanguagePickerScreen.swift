import SwiftUI

/// JSX `LangScreen`: search, flags, tick on the selected row. VOCA main:
/// no Cantonese in the list.
public struct LanguagePickerScreen: View {
    let targetName: String
    let side: Screen.PickSide
    let selected: String
    let onBack: () -> Void
    let onSelect: (String) -> Void

    @State private var query = ""

    public init(targetName: String, side: Screen.PickSide, selected: String,
                onBack: @escaping () -> Void, onSelect: @escaping (String) -> Void) {
        self.targetName = targetName
        self.side = side
        self.selected = selected
        self.onBack = onBack
        self.onSelect = onSelect
    }

    private var accent: Color { side == .b ? VocaTheme.greenDeep : VocaTheme.violet }
    private var tint: Color { side == .b ? VocaTheme.green.opacity(0.1) : VocaTheme.violet.opacity(0.08) }

    public var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 14) {
                HStack(spacing: 12) {
                    BackButton(action: onBack)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(targetName).vocaMicroLabel(9.5, color: accent)
                        Text("Choose language").vocaDisplay(24)
                    }
                    Spacer()
                }
                HStack(spacing: 10) {
                    Icon(.search, size: 16, color: VocaTheme.ink.opacity(0.4))
                    TextField("Search languages", text: $query)
                        .font(VocaTheme.body(15))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("languageSearch")
                }
                .padding(.horizontal, 13)
                .padding(.vertical, 11)
                .background(VocaTheme.ink.opacity(0.05))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .padding(.horizontal, 20)
            .padding(.top, 12)
            .padding(.bottom, 14)
            .background(VocaTheme.surface)
            .overlay(alignment: .bottom) { Rectangle().fill(VocaTheme.hairline).frame(height: 1) }

            ScrollView {
                LazyVStack(spacing: 0) {
                    let results = Languages.search(query)
                    ForEach(results) { lang in
                        Button { onSelect(lang.code) } label: {
                            HStack(spacing: 13) {
                                Text(lang.flag).font(.system(size: 20))
                                    .frame(width: 40, height: 40)
                                    .background(tint)
                                    .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
                                Text(lang.label).vocaBody(15.5, weight: .semibold)
                                Spacer()
                                Text(lang.code.uppercased()).vocaMicroLabel(9.5, color: accent.opacity(0.8), em: 0.1)
                                if selected == lang.code { Icon(.check, size: 18, color: VocaTheme.violet) }
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 14)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(lang.label)
                        .accessibilityAddTraits(selected == lang.code ? [.isButton, .isSelected] : .isButton)
                    }
                    if results.isEmpty {
                        Text("No languages found").vocaBody(14, color: VocaTheme.textFaint)
                            .padding(.vertical, 40)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
            }
        }
        .background(VocaTheme.ground.ignoresSafeArea())
    }
}
