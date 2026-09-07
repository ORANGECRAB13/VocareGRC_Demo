// swift-tools-version: 6.0
import PackageDescription

// VocaKit  — design tokens, API client, entitlement/purchases/ads, history, screens (owner: ios-app)
// VocaSession — WebRTC cloud session, on-device offline pipeline, live split UI (owner: ios-session)
//
// VocaSession depends on VocaKit (tokens, VocaAPI, SessionRecord, PairStatus), never the reverse.
// The app target imports both and composes LiveScreen there.
let package = Package(
    name: "VocaKit",
    defaultLocalization: "en",
    platforms: [.iOS(.v18)],
    products: [
        .library(name: "VocaKit", targets: ["VocaKit"]),
        .library(name: "VocaSession", targets: ["VocaSession"]),
    ],
    dependencies: [
        // Binary WebRTC xcframework. Versions track Chromium milestones, so pin exactly.
        .package(url: "https://github.com/stasel/WebRTC", exact: "152.0.0"),
        // Google Mobile Ads (AdMob). Only ever started when an ad unit id is configured.
        .package(url: "https://github.com/googleads/swift-package-manager-google-mobile-ads", from: "13.9.0"),
    ],
    targets: [
        .target(
            name: "VocaKit",
            dependencies: [
                .product(name: "GoogleMobileAds", package: "swift-package-manager-google-mobile-ads"),
            ],
            resources: [.process("Resources")]
        ),
        .target(
            name: "VocaSession",
            dependencies: [
                "VocaKit",
                .product(name: "WebRTC", package: "WebRTC"),
            ]
        ),
        .testTarget(name: "VocaKitTests", dependencies: ["VocaKit"]),
        .testTarget(name: "VocaSessionTests", dependencies: ["VocaSession"]),
    ],
    swiftLanguageModes: [.v6]
)
