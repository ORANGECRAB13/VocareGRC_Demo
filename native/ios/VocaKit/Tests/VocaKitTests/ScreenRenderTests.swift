import XCTest
import SwiftUI
@testable import VocaKit

/// CONTRACT §8.2: "Snapshot or UI test of Home, Paywall and LiveSplitView."
/// This is the deterministic half of that — it rasterises the two VocaKit
/// screens with `ImageRenderer` at a fixed size and scale and asserts they
/// produce a real, non-blank image. It catches layout traps that only fire at
/// render time (an unsatisfiable frame, a crashing `body`, a missing resource)
/// without pinning us to golden images that churn on every OS update.
@MainActor
final class ScreenRenderTests: XCTestCase {

    private let size = CGSize(width: 393, height: 852)   // iPhone 17 Pro points

    /// Hosts the view in a real (off-screen) window and rasterises the layer.
    /// `ImageRenderer` alone is not enough here: it draws a `ScrollView`'s
    /// container but not its content, which is exactly what the Paywall is.
    private func render<V: View>(_ view: V) throws -> UIImage {
        // RootView paints the ground behind every screen; a screen with no
        // background of its own would otherwise rasterise fully transparent.
        let hosted = ZStack {
            VocaTheme.ground
            view
        }
        let controller = UIHostingController(rootView: hosted)
        controller.view.frame = CGRect(origin: .zero, size: size)
        controller.view.backgroundColor = .white

        let window = UIWindow(frame: CGRect(origin: .zero, size: size))
        window.rootViewController = controller
        window.isHidden = false
        window.makeKeyAndVisible()
        defer { window.isHidden = true }

        controller.view.setNeedsLayout()
        controller.view.layoutIfNeeded()

        let renderer = UIGraphicsImageRenderer(size: size, format: {
            let format = UIGraphicsImageRendererFormat.default()
            format.scale = 1
            format.opaque = true
            return format
        }())
        return renderer.image { context in
            if !controller.view.drawHierarchy(in: controller.view.bounds, afterScreenUpdates: true) {
                controller.view.layer.render(in: context.cgContext)
            }
        }
    }

    /// Rough "did anything actually get drawn" check: sample the bitmap and make
    /// sure it is not one flat colour.
    private func distinctColours(_ image: UIImage, samples: Int = 24) throws -> Int {
        let cg = try XCTUnwrap(image.cgImage)
        let width = cg.width, height = cg.height
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let space = CGColorSpaceCreateDeviceRGB()
        let context = try XCTUnwrap(CGContext(data: &pixels, width: width, height: height,
                                              bitsPerComponent: 8, bytesPerRow: width * 4,
                                              space: space,
                                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        context.draw(cg, in: CGRect(x: 0, y: 0, width: width, height: height))
        var seen = Set<UInt32>()
        for row in stride(from: 0, to: height, by: max(1, height / samples)) {
            for column in stride(from: 0, to: width, by: max(1, width / samples)) {
                let offset = (row * width + column) * 4
                let packed = UInt32(pixels[offset]) << 16 | UInt32(pixels[offset + 1]) << 8 | UInt32(pixels[offset + 2])
                seen.insert(packed)
            }
        }
        return seen.count
    }

    private func assertRendersSomething<V: View>(_ view: V, file: StaticString = #filePath, line: UInt = #line) throws {
        let image = try render(view)
        XCTAssertEqual(image.size.width, size.width, accuracy: 1, file: file, line: line)
        XCTAssertEqual(image.size.height, size.height, accuracy: 1, file: file, line: line)
        let colours = try distinctColours(image)
        XCTAssertGreaterThan(colours, 3, "the screen rendered as a flat colour", file: file, line: line)
    }

    // MARK: Home

    func testHomeRendersForAFreeUser() throws {
        let env = AppEnvironment.preview(balance: Balance(tier: .free, enforced: false))
        try assertRendersSomething(
            HomeScreen(entitlements: env.entitlements, preferences: env.preferences, offline: env.offline,
                       onStart: {}, onPick: { _ in }, onUpgrade: {})
        )
    }

    func testHomeRendersForAProUserWithMinutesLeft() throws {
        let env = AppEnvironment.preview(
            balance: Balance(tier: .pro, secondsTotal: 3600, secondsUsed: 900, secondsRemaining: 2700,
                             durable: true, enforced: true)
        )
        XCTAssertTrue(env.entitlements.isPro)
        try assertRendersSomething(
            HomeScreen(entitlements: env.entitlements, preferences: env.preferences, offline: env.offline,
                       onStart: {}, onPick: { _ in }, onUpgrade: {})
        )
    }

    func testHomeRendersWithAnUnsupportedOfflinePair() throws {
        let env = AppEnvironment.preview(balance: Balance(tier: .free, enforced: true),
                                         offlinePairs: .unsupported)
        env.preferences.langB = "yue"
        try assertRendersSomething(
            HomeScreen(entitlements: env.entitlements, preferences: env.preferences, offline: env.offline,
                       onStart: {}, onPick: { _ in }, onUpgrade: {})
        )
    }

    // MARK: Paywall — all three §5 variants

    func testPaywallRendersEveryVariant() throws {
        for reason in [EntitlementStore.PaywallReason.upsell, .unsupported, .exhausted] {
            let env = AppEnvironment.preview(balance: Balance(tier: .free, enforced: true))
            try assertRendersSomething(
                PaywallScreen(entitlements: env.entitlements, reason: reason,
                              langA: env.preferences.langA, langB: env.preferences.langB,
                              onClose: {}, onPurchased: { _ in })
            )
        }
    }

    func testPaywallShowsTheFallbackPriceWithoutAStorefront() throws {
        let env = AppEnvironment.preview(balance: Balance(tier: .free, enforced: true))
        // `AppEnvironment.preview` wires `PreviewStore`, which has no product.
        XCTAssertEqual(env.entitlements.displayPrice, PurchasesService.planPriceFallback)
        try assertRendersSomething(
            PaywallScreen(entitlements: env.entitlements, reason: .upsell,
                          langA: "en", langB: "zh", onClose: {}, onPurchased: { _ in })
        )
    }
}
