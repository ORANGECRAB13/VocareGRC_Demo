import SwiftUI

// MARK: - Shared building blocks (JSX Section, TabBar, TypingDots, switch, buttons)

public struct SectionHeader: View {
    let title: String
    public init(_ title: String) { self.title = title }
    public var body: some View {
        Text(title)
            .vocaMicroLabel(10.5, color: VocaTheme.inkMute, em: 0.16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 9)
    }
}

/// White card with the canvas hairline and soft shadow.
public struct VocaCard<Content: View>: View {
    let radius: CGFloat
    let padding: CGFloat
    let content: Content

    public init(radius: CGFloat = VocaTheme.cardRadius, padding: CGFloat = 0, @ViewBuilder content: () -> Content) {
        self.radius = radius
        self.padding = padding
        self.content = content()
    }

    public var body: some View {
        content
            .padding(padding)
            .background(VocaTheme.surface)
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).stroke(VocaTheme.hairline, lineWidth: 1))
    }
}

/// The 44×26 pill switch drawn on the canvas.
public struct VocaSwitch: View {
    @Binding var isOn: Bool
    let label: String

    public init(isOn: Binding<Bool>, label: String) {
        self._isOn = isOn
        self.label = label
    }

    public var body: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) { isOn.toggle() }
        } label: {
            ZStack(alignment: isOn ? .trailing : .leading) {
                Capsule().fill(isOn ? VocaTheme.violet : VocaTheme.ink.opacity(0.16))
                Circle()
                    .fill(Color.white)
                    .shadow(color: .black.opacity(0.25), radius: 1.5, y: 1)
                    .frame(width: 20, height: 20)
                    .padding(3)
            }
            .frame(width: 44, height: 26)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(isOn ? "On" : "Off")
    }
}

/// The violet 62pt CTA with the glow shadow.
public struct PrimaryButton: View {
    let title: String
    let icon: Icon.Name?
    let busy: Bool
    let action: () -> Void

    public init(_ title: String, icon: Icon.Name? = nil, busy: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.icon = icon
        self.busy = busy
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                if let icon { Icon(icon, size: 19, color: .white) }
                Text(title).vocaBody(17, weight: .semibold, color: .white)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 62)
            .background(VocaTheme.violet)
            .clipShape(RoundedRectangle(cornerRadius: VocaTheme.buttonRadius, style: .continuous))
            .shadow(color: VocaTheme.violet.opacity(0.6), radius: 13, y: 12)
            .opacity(busy ? 0.6 : 1)
        }
        .buttonStyle(.plain)
        .disabled(busy)
    }
}

public struct SecondaryButton: View {
    let title: String
    let action: () -> Void

    public init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            Text(title).vocaBody(16, weight: .semibold)
                .frame(maxWidth: .infinity)
                .frame(height: 56)
                .background(VocaTheme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(VocaTheme.hairline, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }
}

/// 34×34 rounded back chevron.
public struct BackButton: View {
    let light: Bool
    let action: () -> Void

    public init(light: Bool = false, action: @escaping () -> Void) {
        self.light = light
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            Icon(.chevronLeft, size: 17, color: light ? VocaTheme.ground : VocaTheme.ink)
                .frame(width: 34, height: 34)
                .background(light ? VocaTheme.ground.opacity(0.1) : VocaTheme.ink.opacity(0.05))
                .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Back")
    }
}

/// Three blinking dots.
public struct TypingDots: View {
    let color: Color
    @State private var phase = false

    public init(color: Color = VocaTheme.textMuted) { self.color = color }

    public var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { i in
                Circle().fill(color).frame(width: 6, height: 6)
                    .opacity(phase ? 1 : 0.3)
                    .animation(.easeInOut(duration: 0.6).repeatForever().delay(Double(i) * 0.2), value: phase)
            }
        }
        .onAppear { phase = true }
    }
}

/// A settings-style toggle row.
public struct ToggleRow: View {
    let title: String
    let hint: String
    @Binding var isOn: Bool

    public init(_ title: String, hint: String, isOn: Binding<Bool>) {
        self.title = title
        self.hint = hint
        self._isOn = isOn
    }

    public var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).vocaBody(14.5, weight: .semibold)
                Text(hint).vocaBody(11.5, color: VocaTheme.ink.opacity(0.48))
            }
            Spacer(minLength: 0)
            VocaSwitch(isOn: $isOn, label: title)
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 14)
    }
}

public struct TabBar: View {
    let active: Tab
    let onChange: (Tab) -> Void

    public init(active: Tab, onChange: @escaping (Tab) -> Void) {
        self.active = active
        self.onChange = onChange
    }

    private let items: [(Tab, Icon.Name, String)] = [
        (.home, .home, "Home"),
        (.translate, .mic, "Translate"),
        (.history, .history, "History"),
        (.settings, .settings, "Settings"),
    ]

    public var body: some View {
        HStack(spacing: 0) {
            ForEach(items, id: \.0) { item in
                let isActive = active == item.0
                Button { onChange(item.0) } label: {
                    VStack(spacing: 5) {
                        Icon(item.1, size: 22, color: isActive ? VocaTheme.violet : VocaTheme.ink.opacity(0.38))
                        Text(item.2)
                            .font(VocaTheme.body(10.5, weight: .semibold))
                            .tracking(0.2)
                            .foregroundStyle(isActive ? VocaTheme.violet : VocaTheme.ink.opacity(0.38))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 11)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(item.2)
                .accessibilityAddTraits(isActive ? [.isButton, .isSelected] : .isButton)
            }
        }
        .frame(height: 82, alignment: .top)
        .background(VocaTheme.ground.opacity(0.92).background(.ultraThinMaterial))
        .overlay(alignment: .top) { Rectangle().fill(VocaTheme.hairline).frame(height: 1) }
    }
}

/// Screen title in Outfit 34.
public struct ScreenTitle: View {
    let text: String
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text).vocaDisplay(34)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 22)
            .padding(.top, 18)
            .padding(.bottom, 14)
    }
}

/// Opens an https link in the system browser.
public struct LinkRow: View {
    let title: String
    let hint: String
    let url: URL

    public init(_ title: String, hint: String, url: URL) {
        self.title = title
        self.hint = hint
        self.url = url
    }

    public var body: some View {
        Link(destination: url) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).vocaBody(14.5, weight: .semibold)
                    Text(hint).vocaBody(11.5, color: VocaTheme.ink.opacity(0.48))
                }
                Spacer(minLength: 0)
                Icon(.chevron, size: 16, color: VocaTheme.ink.opacity(0.3))
            }
            .padding(.horizontal, 15)
            .padding(.vertical, 14)
        }
    }
}
