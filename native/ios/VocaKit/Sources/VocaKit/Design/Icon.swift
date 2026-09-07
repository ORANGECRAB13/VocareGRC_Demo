import SwiftUI

/// The JSX `ICONS` table, mapped onto SF Symbols. Same names so the screens
/// read like the reference.
public struct Icon: View {
    public enum Name: String, CaseIterable, Sendable {
        case mic, micOff, stop, pause, arrowRight, swap, history, home, settings
        case chevron, chevronLeft, check, wifiOff, globe, volume, clock, x, alert, search, plus, translate

        public var symbol: String {
            switch self {
            case .mic: return "mic.fill"
            case .micOff: return "mic.slash.fill"
            case .stop: return "stop.fill"
            case .pause: return "pause.fill"
            case .arrowRight: return "arrow.right"
            case .swap: return "arrow.left.arrow.right"
            case .history: return "clock.arrow.circlepath"
            case .home: return "house.fill"
            case .settings: return "gearshape.fill"
            case .chevron: return "chevron.right"
            case .chevronLeft: return "chevron.left"
            case .check: return "checkmark"
            case .wifiOff: return "wifi.slash"
            case .globe: return "globe"
            case .volume: return "speaker.wave.2.fill"
            case .clock: return "clock.fill"
            case .x: return "xmark"
            case .alert: return "exclamationmark.circle.fill"
            case .search: return "magnifyingglass"
            case .plus: return "plus"
            case .translate: return "character.book.closed.fill"
            }
        }
    }

    public let name: Name
    public let size: CGFloat
    public let color: Color?
    public let weight: Font.Weight

    public init(_ name: Name, size: CGFloat = 24, color: Color? = nil, weight: Font.Weight = .semibold) {
        self.name = name
        self.size = size
        self.color = color
        self.weight = weight
    }

    public var body: some View {
        Image(systemName: name.symbol)
            .font(.system(size: size * 0.78, weight: weight))
            .frame(width: size, height: size)
            .foregroundStyle(color ?? Color.primary)
            .accessibilityHidden(true)
    }
}
