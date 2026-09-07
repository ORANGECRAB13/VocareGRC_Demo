package com.vocare.translate.session.cloud

import android.content.Context
import com.vocare.translate.core.model.IceServer
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.DataChannel
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.audio.JavaAudioDeviceModule
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/**
 * Owns the process-wide `PeerConnectionFactory` and the single microphone
 * source. Each `create` hands out one leg carrying its own `AudioTrack` from
 * that source — libwebrtc's equivalent of the JSX cloning the mic track once
 * per leg (CONTRACT §3.3): both tracks are fed by one capture but are enabled
 * and disabled independently.
 *
 * Requires `RECORD_AUDIO` to have been granted before `create` is called.
 */
class WebRtcLegFactory(context: Context) : PeerLegFactory {
    private val factory: PeerConnectionFactory
    private val audioSource: AudioSource
    private var trackSerial = 0

    init {
        val appContext = context.applicationContext
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(appContext)
                .createInitializationOptions(),
        )
        val adm = JavaAudioDeviceModule.builder(appContext)
            .setUseHardwareAcousticEchoCanceler(true)
            .setUseHardwareNoiseSuppressor(true)
            .createAudioDeviceModule()
        factory = PeerConnectionFactory.builder()
            .setAudioDeviceModule(adm)
            .createPeerConnectionFactory()
        adm.release()
        // JSX getUserMedia constraints: echoCancellation, noiseSuppression, autoGainControl.
        audioSource = factory.createAudioSource(
            MediaConstraints().apply {
                mandatory.add(MediaConstraints.KeyValuePair("googEchoCancellation", "true"))
                mandatory.add(MediaConstraints.KeyValuePair("googNoiseSuppression", "true"))
                mandatory.add(MediaConstraints.KeyValuePair("googAutoGainControl", "true"))
            },
        )
    }

    override fun create(iceServers: List<IceServer>, onConnectionState: (LegConnectionState) -> Unit): PeerLeg {
        val config = PeerConnection.RTCConfiguration(iceServers.map { it.toRtc() }).apply {
            // TURN only — the server strips non-relay candidates from its answer anyway.
            iceTransportsType = PeerConnection.IceTransportsType.RELAY
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
        }
        val track = factory.createAudioTrack("voca-mic-${trackSerial++}", audioSource)
        return WebRtcLeg(factory, config, track, onConnectionState)
    }

    /** Release the factory and mic source once no session is running. */
    fun dispose() {
        audioSource.dispose()
        factory.dispose()
    }

    private fun IceServer.toRtc(): PeerConnection.IceServer {
        val builder = PeerConnection.IceServer.builder(urls)
        username?.takeIf { it.isNotEmpty() }?.let { builder.setUsername(it) }
        credential?.takeIf { it.isNotEmpty() }?.let { builder.setPassword(it) }
        return builder.createIceServer()
    }
}

/** A real libwebrtc peer connection wrapped as a `PeerLeg`. */
class WebRtcLeg(
    factory: PeerConnectionFactory,
    config: PeerConnection.RTCConfiguration,
    private val micTrack: AudioTrack,
    private val onConnectionState: (LegConnectionState) -> Unit,
) : PeerLeg {
    private val iceComplete = CompletableDeferred<Unit>()
    private val closed = AtomicBoolean(false)
    private var remoteTrack: AudioTrack? = null

    private val observer = object : PeerConnection.Observer {
        override fun onSignalingChange(state: PeerConnection.SignalingState) {}
        override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) {}
        override fun onIceConnectionReceivingChange(receiving: Boolean) {}
        override fun onIceCandidate(candidate: IceCandidate) {}
        override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) {}
        override fun onAddStream(stream: MediaStream) {}
        override fun onRemoveStream(stream: MediaStream) {}
        override fun onDataChannel(channel: DataChannel) {}
        override fun onRenegotiationNeeded() {}

        override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) {
            if (state == PeerConnection.IceGatheringState.COMPLETE) iceComplete.complete(Unit)
        }

        override fun onConnectionChange(newState: PeerConnection.PeerConnectionState) {
            onConnectionState(
                when (newState) {
                    PeerConnection.PeerConnectionState.NEW -> LegConnectionState.NEW
                    PeerConnection.PeerConnectionState.CONNECTING -> LegConnectionState.CONNECTING
                    PeerConnection.PeerConnectionState.CONNECTED -> LegConnectionState.CONNECTED
                    PeerConnection.PeerConnectionState.DISCONNECTED -> LegConnectionState.DISCONNECTED
                    PeerConnection.PeerConnectionState.FAILED -> LegConnectionState.FAILED
                    PeerConnection.PeerConnectionState.CLOSED -> LegConnectionState.CLOSED
                },
            )
        }

        // JSX `pc.ontrack`: play whatever the server sends back on this leg. The audio
        // device module renders every enabled remote AudioTrack, so enabling is playout.
        override fun onAddTrack(receiver: RtpReceiver, mediaStreams: Array<out MediaStream>) {
            (receiver.track() as? AudioTrack)?.let {
                it.setEnabled(true)
                remoteTrack = it
            }
        }
    }

    private val pc: PeerConnection = requireNotNull(factory.createPeerConnection(config, observer)) {
        "PeerConnectionFactory.createPeerConnection returned null"
    }

    override fun setMicEnabled(enabled: Boolean) {
        if (!closed.get()) micTrack.setEnabled(enabled)
    }

    override suspend fun createOffer(): LocalOffer {
        // Added enabled: negotiating with a disabled track can leave it permanently
        // silent when enabled later (JSX §3.3 note). The view model disables it once
        // both answers are applied.
        micTrack.setEnabled(true)
        pc.addTrack(micTrack, listOf("voca-${micTrack.id()}"))
        val offer = suspendCoroutine<SessionDescription> { cont ->
            pc.createOffer(
                object : SdpAdapter() {
                    override fun onCreateSuccess(description: SessionDescription) = cont.resume(description)
                    override fun onCreateFailure(error: String?) =
                        cont.resumeWithException(IllegalStateException("createOffer failed: $error"))
                },
                MediaConstraints().apply {
                    mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveAudio", "true"))
                    mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveVideo", "false"))
                },
            )
        }
        setDescription(local = true, description = offer)
        // JSX waitForICE: resolve on `complete`, or after 5s regardless.
        if (pc.iceGatheringState() != PeerConnection.IceGatheringState.COMPLETE) {
            withTimeoutOrNull(ICE_GATHER_CAP_MS) { iceComplete.await() }
        }
        val local = requireNotNull(pc.localDescription) { "no local description" }
        return LocalOffer(sdp = local.description, type = local.type.canonicalForm())
    }

    override suspend fun setAnswer(sdp: String, type: String) {
        val answer = SessionDescription(SessionDescription.Type.fromCanonicalForm(type), sdp)
        setDescription(local = false, description = answer)
    }

    private suspend fun setDescription(local: Boolean, description: SessionDescription) {
        suspendCoroutine<Unit> { cont ->
            val observer = object : SdpAdapter() {
                override fun onSetSuccess() = cont.resume(Unit)
                override fun onSetFailure(error: String?) =
                    cont.resumeWithException(IllegalStateException("set${if (local) "Local" else "Remote"}Description failed: $error"))
            }
            if (local) pc.setLocalDescription(observer, description) else pc.setRemoteDescription(observer, description)
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        iceComplete.cancel()
        runCatching { remoteTrack?.setEnabled(false) }
        runCatching { micTrack.setEnabled(false) }
        runCatching { pc.close() }
        runCatching { pc.dispose() }
        runCatching { micTrack.dispose() }
    }

    private abstract class SdpAdapter : SdpObserver {
        override fun onCreateSuccess(description: SessionDescription) {}
        override fun onSetSuccess() {}
        override fun onCreateFailure(error: String?) {}
        override fun onSetFailure(error: String?) {}
    }

    private companion object {
        const val ICE_GATHER_CAP_MS = 5_000L
    }
}
