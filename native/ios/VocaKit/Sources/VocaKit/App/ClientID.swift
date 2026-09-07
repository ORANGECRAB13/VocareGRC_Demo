import Foundation

/// Opaque per-install id (JSX `clientId()`). Not an identity or a credential —
/// it only scopes history and the credit balance to this install.
public enum ClientID {
    public static let key = "vocare.clientId"

    public static func current(defaults: UserDefaults = .standard) -> String {
        if let existing = defaults.string(forKey: key), !existing.isEmpty { return existing }
        let fresh = UUID().uuidString.lowercased()
        defaults.set(fresh, forKey: key)
        return fresh
    }
}
