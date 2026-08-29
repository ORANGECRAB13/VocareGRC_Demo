@preconcurrency import AVFoundation
import Combine
import Foundation

@MainActor
final class WatchAudioController: NSObject, ObservableObject {
    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var recordingURL: URL?
    var onPlaybackFinished: (() -> Void)?

    func startRecording() async throws {
        NSLog("[Watch] requesting microphone permission")
        guard await microphonePermission() else { throw AudioError.permissionDenied }
        NSLog("[Watch] microphone permission granted")

        let session = AVAudioSession.sharedInstance()
        // .default (not .measurement) keeps the system's input processing and
        // gain control, which wrist-distance speech needs for usable levels.
        try session.setCategory(.record, mode: .default, options: [])
        try session.setActive(true)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("vocare-watch-\(UUID().uuidString).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let recorder = try AVAudioRecorder(url: url, settings: settings)
        guard recorder.prepareToRecord() else { throw AudioError.couldNotRecord }
        guard recorder.record() else { throw AudioError.couldNotRecord }
        recordingURL = url
        self.recorder = recorder
    }

    func stopRecording() throws -> Data {
        recorder?.stop()
        recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        guard let url = recordingURL else { throw AudioError.noRecording }
        recordingURL = nil
        defer { try? FileManager.default.removeItem(at: url) }
        return try Data(contentsOf: url)
    }

    func cancelRecording() {
        recorder?.stop()
        recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        if let url = recordingURL {
            try? FileManager.default.removeItem(at: url)
        }
        recordingURL = nil
    }

    func stopPlayback() {
        player?.stop()
        player = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func play(wavData: Data) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .spokenAudio, options: [])
        try session.setActive(true)
        player = try AVAudioPlayer(data: wavData)
        player?.delegate = self
        guard player?.prepareToPlay() == true, player?.play() == true else {
            throw AudioError.couldNotPlay
        }
    }

    private func microphonePermission() async -> Bool {
        if #available(watchOS 10.0, *) {
            return await AVAudioApplication.requestRecordPermission()
        }
        return await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { allowed in
                continuation.resume(returning: allowed)
            }
        }
    }

    enum AudioError: LocalizedError {
        case permissionDenied, couldNotRecord, noRecording, couldNotPlay

        var errorDescription: String? {
            switch self {
            case .permissionDenied: return "Microphone permission is required."
            case .couldNotRecord: return "The watch could not begin recording."
            case .noRecording: return "No recorded speech was available."
            case .couldNotPlay: return "The translated audio could not be played."
            }
        }
    }
}

extension WatchAudioController: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            self?.onPlaybackFinished?()
        }
    }
}
