import AVFoundation
import Foundation
import VocaKit
@preconcurrency import WebRTC

// MARK: - Real WebRTC leg (CONTRACT §3 steps 2–5)
//
// Everything libwebrtc-specific lives here. The peer connection is created
// with `iceTransportPolicy = .relay` (TURN only — the server strips non-relay
// candidates from its answer anyway), carries exactly one audio track and no
// data channels, and plays whatever remote track arrives through the shared
// RTCAudioSession. Two legs share one `RTCAudioSource`, which is the libwebrtc
// equivalent of `sourceTrack.clone()` in the JSX: same capture, independently
// enable-able tracks.

@MainActor
public final class WebRTCLeg: NSObject, PeerLeg {
    public var onConnectionState: ((PeerConnectionState) -> Void)?

    private let pc: RTCPeerConnection
    private let track: RTCAudioTrack
    private var remoteTrack: RTCAudioTrack?
    private var iceWaiters: [CheckedContinuation<Void, Never>] = []
    private var closed = false

    /// - Parameters:
    ///   - factory: the process-wide peer connection factory.
    ///   - source: the shared microphone source (one per session, shared by both legs).
    ///   - iceServers: from `GET /api/ice`.
    init(factory: RTCPeerConnectionFactory, source: RTCAudioSource, iceServers: [ICEServer]) throws {
        let config = RTCConfiguration()
        config.iceServers = iceServers.map { server in
            RTCIceServer(urlStrings: server.urls, username: server.username, credential: server.credential)
        }
        config.iceTransportPolicy = .relay
        config.sdpSemantics = .unifiedPlan
        config.continualGatheringPolicy = .gatherOnce

        let constraints = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil)
        guard let pc = factory.peerConnection(with: config, constraints: constraints, delegate: nil) else {
            throw CloudSessionError.noIceServers
        }
        self.pc = pc
        // Per-leg clone of the mic. Enabled during negotiation on purpose
        // (§3 step 3); the view model disables it once both answers are applied.
        self.track = factory.audioTrack(with: source, trackId: "audio-\(UUID().uuidString)")
        self.track.isEnabled = true
        super.init()
        pc.delegate = self
    }

    public func createOffer() async throws -> LocalOffer {
        let streamId = "stream-\(UUID().uuidString)"
        pc.add(track, streamIds: [streamId])

        let constraints = RTCMediaConstraints(
            mandatoryConstraints: ["OfferToReceiveAudio": "true", "OfferToReceiveVideo": "false"],
            optionalConstraints: nil
        )
        let offer = try await pc.offer(for: constraints)
        try await pc.setLocalDescription(offer)
        await waitForICE()
        guard let local = pc.localDescription else { throw CloudSessionError.notConnected }
        return LocalOffer(sdp: local.sdp, type: RTCSessionDescription.string(for: local.type))
    }

    public func setRemoteAnswer(sdp: String, type: String) async throws {
        let description = RTCSessionDescription(type: RTCSessionDescription.type(for: type), sdp: sdp)
        try await pc.setRemoteDescription(description)
    }

    public func setTrackEnabled(_ enabled: Bool) {
        track.isEnabled = enabled
    }

    public func close() {
        guard !closed else { return }
        closed = true
        onConnectionState = nil
        track.isEnabled = false
        remoteTrack?.isEnabled = false
        remoteTrack = nil
        pc.delegate = nil
        pc.close()
        resumeIceWaiters()
    }

    // MARK: ICE gathering (JSX `waitForICE`, 5s cap)

    private func waitForICE() async {
        if pc.iceGatheringState == .complete { return }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            iceWaiters.append(continuation)
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                self?.resumeIceWaiters()
            }
        }
    }

    private func resumeIceWaiters() {
        let waiters = iceWaiters
        iceWaiters.removeAll()
        for waiter in waiters { waiter.resume() }
    }

    fileprivate func handleGathering(_ state: RTCIceGatheringState) {
        if state == .complete { resumeIceWaiters() }
    }

    fileprivate func handleConnection(_ state: RTCPeerConnectionState) {
        guard !closed else { return }
        let mapped: PeerConnectionState
        switch state {
        case .new: mapped = .new
        case .connecting: mapped = .connecting
        case .connected: mapped = .connected
        case .disconnected: mapped = .disconnected
        case .failed: mapped = .failed
        case .closed: mapped = .closed
        @unknown default: mapped = .failed
        }
        onConnectionState?(mapped)
    }

    fileprivate func handleRemoteTrack(_ track: RTCAudioTrack) {
        guard !closed else { return }
        // Keeping a reference is what keeps it playing; libwebrtc routes remote
        // audio to the RTCAudioSession output on its own.
        remoteTrack = track
        track.isEnabled = true
    }
}

// MARK: RTCPeerConnectionDelegate
//
// libwebrtc calls these on its signalling thread; every one hops to the main
// actor before touching state. Only enum values and the received track cross
// the boundary.

extension WebRTCLeg: RTCPeerConnectionDelegate {
    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didChange stateChanged: RTCSignalingState) {}

    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didAdd stream: RTCMediaStream) {
        guard let audio = stream.audioTracks.first else { return }
        Task { @MainActor in self.handleRemoteTrack(audio) }
    }

    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didRemove stream: RTCMediaStream) {}
    nonisolated public func peerConnectionShouldNegotiate(_ peerConnection: RTCPeerConnection) {}
    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceConnectionState) {}

    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceGatheringState) {
        Task { @MainActor in self.handleGathering(newState) }
    }

    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCPeerConnectionState) {
        Task { @MainActor in self.handleConnection(newState) }
    }

    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {}
    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {}
    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {}

    nonisolated public func peerConnection(_ peerConnection: RTCPeerConnection, didAdd rtpReceiver: RTCRtpReceiver, streams mediaStreams: [RTCMediaStream]) {
        guard let audio = rtpReceiver.track as? RTCAudioTrack else { return }
        Task { @MainActor in self.handleRemoteTrack(audio) }
    }
}

// MARK: - Factory

/// Owns the process-wide `RTCPeerConnectionFactory` and one microphone source
/// per session. Also puts AVAudioSession into play-and-record on the speaker,
/// which is what a phone lying flat on a table between two people wants.
@MainActor
public final class WebRTCLegFactory: PeerLegFactory {
    private static let initialised: Bool = {
        RTCInitializeSSL()
        return true
    }()

    private let factory: RTCPeerConnectionFactory
    private var source: RTCAudioSource?

    public init() {
        _ = WebRTCLegFactory.initialised
        factory = RTCPeerConnectionFactory(
            encoderFactory: RTCDefaultVideoEncoderFactory(),
            decoderFactory: RTCDefaultVideoDecoderFactory()
        )
        WebRTCLegFactory.configureAudioSession()
    }

    public func makeLeg(iceServers: [ICEServer]) throws -> PeerLeg {
        let source = self.source ?? {
            let constraints = RTCMediaConstraints(
                mandatoryConstraints: nil,
                optionalConstraints: [
                    "googEchoCancellation": "true",
                    "googNoiseSuppression": "true",
                    "googAutoGainControl": "true",
                ]
            )
            let created = factory.audioSource(with: constraints)
            self.source = created
            return created
        }()
        return try WebRTCLeg(factory: factory, source: source, iceServers: iceServers)
    }

    private static func configureAudioSession() {
        let session = RTCAudioSession.sharedInstance()
        session.lockForConfiguration()
        defer { session.unlockForConfiguration() }
        do {
            try session.setCategory(.playAndRecord, mode: .voiceChat,
                                    options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true)
        } catch {
            // Non-fatal: libwebrtc will configure a usable session itself; we
            // only lose the speaker default.
        }
    }
}

// MARK: - AVFoundation microphone permission

public struct AVMicrophonePermission: MicrophonePermission {
    public init() {}

    public func request() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }
}
