import Foundation
import SwiftUI
import GoogleMobileAds

/// Free-tier banner (JSX `showFreeTierBanner`). Starting the Google Mobile Ads
/// SDK without `GADApplicationIdentifier` in Info.plist raises an uncaught
/// ObjC exception that kills the app, so this service refuses to touch the
/// SDK unless BOTH the app id and a banner unit id are configured. A missing
/// ad id must cost us a banner, never the session someone is in.
@MainActor
public final class AdsService: ObservableObject {
    public static let infoPlistAppIDKey = "GADApplicationIdentifier"
    public static let infoPlistBannerKey = "VOCARE_ADMOB_IOS_BANNER"
    /// Google's public test banner unit.
    public static let testBannerUnitID = "ca-app-pub-3940256099942544/2934735716"

    public let bannerUnitID: String?
    public let applicationID: String?
    @Published public private(set) var started = false

    public init(bannerUnitID: String?, applicationID: String?) {
        self.bannerUnitID = bannerUnitID?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
        self.applicationID = applicationID?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
    }

    /// Reads both ids from the app's Info.plist.
    public convenience init(bundle: Bundle = .main) {
        self.init(bannerUnitID: bundle.object(forInfoDictionaryKey: AdsService.infoPlistBannerKey) as? String,
                  applicationID: bundle.object(forInfoDictionaryKey: AdsService.infoPlistAppIDKey) as? String)
    }

    /// Both ids present — the only state in which the SDK is ever initialised.
    public var isConfigured: Bool { bannerUnitID != nil && applicationID != nil }

    /// Idempotent. No-op when unconfigured.
    public func startIfConfigured() {
        guard isConfigured, !started else { return }
        started = true
        MobileAds.shared.start(completionHandler: nil)
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}

/// A 320×50 adaptive banner. Renders nothing at all unless the service is
/// configured, so screens can drop it in unconditionally.
public struct BannerAdView: View {
    @ObservedObject private var ads: AdsService

    public init(ads: AdsService) {
        self.ads = ads
    }

    public var body: some View {
        if ads.isConfigured, let unit = ads.bannerUnitID {
            BannerRepresentable(unitID: unit, ads: ads)
                .frame(height: 50)
                .frame(maxWidth: .infinity)
        }
    }
}

private struct BannerRepresentable: UIViewRepresentable {
    let unitID: String
    let ads: AdsService

    func makeUIView(context: Context) -> BannerView {
        ads.startIfConfigured()
        let view = BannerView(adSize: AdSizeBanner)
        view.adUnitID = unitID
        view.rootViewController = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow?.rootViewController }
            .first
        view.load(Request())
        return view
    }

    func updateUIView(_ uiView: BannerView, context: Context) {}
}
