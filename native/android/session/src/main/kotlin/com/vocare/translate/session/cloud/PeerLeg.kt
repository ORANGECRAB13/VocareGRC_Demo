package com.vocare.translate.session.cloud

import com.vocare.translate.core.model.IceServer

/** Mirror of the subset of `RTCPeerConnectionState` the session cares about. */
enum class LegConnectionState { NEW, CONNECTING, CONNECTED, DISCONNECTED, FAILED, CLOSED }

/** A local session description as the backend wants it. */
data class LocalOffer(val sdp: String, val type: String = "offer")

/**
 * One participant leg: a peer connection carrying a clone of the microphone up
 * and the translation for that person back down.
 *
 * `WebRtcLeg` is the real thing; tests substitute a fake so the view model's
 * protocol (CONTRACT §3) can be exercised without libwebrtc.
 */
interface PeerLeg {
    /** Turn this leg's microphone clone on or off (push-to-talk owns this once connected). */
    fun setMicEnabled(enabled: Boolean)

    /**
     * Add the mic track, create the offer, set it locally and wait for ICE gathering
     * to complete (capped at 5s, JSX `waitForICE`). Returns the local description.
     */
    suspend fun createOffer(): LocalOffer

    suspend fun setAnswer(sdp: String, type: String)

    /** Stop the track and close the connection. Idempotent. */
    fun close()
}

/** Creates legs; both legs of a session share the same ICE servers and microphone. */
fun interface PeerLegFactory {
    /**
     * @param onConnectionState called (on any thread) whenever the leg's connection state changes.
     */
    fun create(iceServers: List<IceServer>, onConnectionState: (LegConnectionState) -> Unit): PeerLeg
}
