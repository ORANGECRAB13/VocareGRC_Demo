import Foundation
import VocaKit

// MARK: - Peer connection abstraction
//
// One "leg" is one RTCPeerConnection plus the microphone clone that feeds it
// (CONTRACT §3 step 2–5). The view model only ever needs five things from a
// leg, so that is the whole protocol; `WebRTCLeg` is the real thing and the
// tests use an in-memory fake, which is how the state machine gets exercised
// without ever touching libwebrtc.

public enum PeerConnectionState: Equatable, Sendable {
    case new
    case connecting
    case connected
    case disconnected
    case failed
    case closed
}

public struct LocalOffer: Equatable, Sendable {
    public let sdp: String
    public let type: String

    public init(sdp: String, type: String = "offer") {
        self.sdp = sdp
        self.type = type
    }
}

@MainActor
public protocol PeerLeg: AnyObject {
    /// Fired on the main actor whenever the underlying connection state changes.
    var onConnectionState: ((PeerConnectionState) -> Void)? { get set }

    /// Add the (enabled) mic clone, create + set the local offer, wait for ICE
    /// gathering to finish (capped at 5s) and return what to POST to `/offer`.
    func createOffer() async throws -> LocalOffer

    /// Apply the server's answer.
    func setRemoteAnswer(sdp: String, type: String) async throws

    /// Mute/unmute this leg's mic clone. Push-to-talk flips this.
    func setTrackEnabled(_ enabled: Bool)

    /// Stop the local track, drop the remote audio, close the connection.
    func close()
}

@MainActor
public protocol PeerLegFactory {
    func makeLeg(iceServers: [ICEServer]) throws -> PeerLeg
}

// MARK: - Microphone permission

/// The one bit of AVFoundation the view model needs before anything else
/// (CONTRACT §3 step 1). Injected so tests can say "denied".
public protocol MicrophonePermission: Sendable {
    func request() async -> Bool
}

public enum CloudSessionError: Error, Equatable, Sendable {
    case notConnected
    case noIceServers
    case connectionLost
}
