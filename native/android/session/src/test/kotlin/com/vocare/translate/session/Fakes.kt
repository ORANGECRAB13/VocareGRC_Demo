package com.vocare.translate.session

import kotlinx.coroutines.delay

import com.vocare.translate.core.api.ApiException
import com.vocare.translate.core.api.VocaApi
import com.vocare.translate.core.history.SessionHistoryStore
import com.vocare.translate.core.model.Balance
import com.vocare.translate.core.model.CreateSessionRequest
import com.vocare.translate.core.model.IceServer
import com.vocare.translate.core.model.OfferAnswer
import com.vocare.translate.core.model.OfferRequest
import com.vocare.translate.core.model.PollResponse
import com.vocare.translate.core.model.PttAction
import com.vocare.translate.core.model.PttResult
import com.vocare.translate.core.model.SessionRecord
import com.vocare.translate.core.offline.OfflineEngine
import com.vocare.translate.core.offline.PairStatus
import com.vocare.translate.core.offline.Recognition
import com.vocare.translate.session.cloud.LegConnectionState
import com.vocare.translate.session.cloud.LocalOffer
import com.vocare.translate.session.cloud.PeerLeg
import com.vocare.translate.session.cloud.PeerLegFactory
import kotlinx.coroutines.yield
import org.junit.Assert.assertTrue
import java.io.File

/**
 * `native/fixtures` JSON files, loaded from the repository rather than from a
 * classpath resource that could silently be absent. A fixture that resolves to
 * nothing makes every test that reads it pass for the wrong reason, so the
 * loader searches upwards, fails loudly, and asserts the file is not empty.
 */
object Fixtures {
    private val root: File by lazy {
        var dir: File? = File("").absoluteFile
        while (dir != null) {
            val candidate = File(dir, "native/fixtures")
            if (candidate.isDirectory) return@lazy candidate
            dir = dir.parentFile
        }
        throw IllegalStateException(
            "native/fixtures not found above ${File("").absolutePath} — fixtures must be read, not assumed",
        )
    }

    fun read(name: String): String {
        val file = File(root, name)
        assertTrue("fixture $name does not exist at ${file.absolutePath}", file.isFile)
        val text = file.readText()
        assertTrue("fixture $name is empty at ${file.absolutePath}", text.isNotBlank())
        return text
    }
}

/** A leg that records what the view model did to it instead of talking to libwebrtc. */
class FakeLeg(private val onState: (LegConnectionState) -> Unit) : PeerLeg {
    /** Named to avoid a JVM signature clash with `setMicEnabled` from [PeerLeg]. */
    var micOn: Boolean = true
    var closed: Boolean = false
    val micHistory = mutableListOf<Boolean>()
    var offersCreated = 0
    var answerSet: String? = null

    override fun setMicEnabled(enabled: Boolean) {
        micOn = enabled
        micHistory += enabled
    }

    override suspend fun createOffer(): LocalOffer {
        offersCreated++
        return LocalOffer("v=0 fake offer")
    }

    override suspend fun setAnswer(sdp: String, type: String) {
        answerSet = sdp
    }

    override fun close() {
        closed = true
    }

    fun report(state: LegConnectionState) = onState(state)
}

class FakeLegFactory : PeerLegFactory {
    val legs = mutableListOf<FakeLeg>()

    override fun create(iceServers: List<IceServer>, onConnectionState: (LegConnectionState) -> Unit): PeerLeg =
        FakeLeg(onConnectionState).also { legs += it }

    /** Leg A is created first (CONTRACT §3.2). */
    val legA: FakeLeg get() = legs[0]
    val legB: FakeLeg get() = legs[1]
}

/** Scripted [VocaApi]: poll frames are drained one per call, everything else is recorded. */
class FakeVocaApi(
    var ice: List<IceServer> = listOf(IceServer(listOf("turn:example:3478"), "u", "c")),
    var pollFrames: MutableList<PollResponse> = mutableListOf(),
) : VocaApi {
    var offerFailsWithPaymentRequired = false
    var pttThrows = false
    var flushedBytesOnRelease: Long? = 12800L

    val pttCalls = mutableListOf<Pair<String, PttAction>>()
    val hangups = mutableListOf<String>()
    val consumes = mutableListOf<Int>()
    var createdSessions = 0
    var pollCalls = 0
    private var pcSerial = 0

    override suspend fun iceServers(): List<IceServer> {
        if (ice.isEmpty()) throw ApiException.NoIceServers()
        return ice
    }

    override suspend fun createSession(req: CreateSessionRequest): String {
        createdSessions++
        return "session-1"
    }

    override suspend fun offer(req: OfferRequest): OfferAnswer {
        if (offerFailsWithPaymentRequired) throw ApiException.PaymentRequired(null)
        pcSerial++
        return OfferAnswer(pcId = if (pcSerial == 1) PC_A else PC_B, sdp = "v=0 answer")
    }

    override suspend fun ptt(pcId: String, action: PttAction): PttResult {
        pttCalls += pcId to action
        if (pttThrows) throw ApiException.Http(500, "gate_failed")
        return if (action == PttAction.HOLD) {
            PttResult(ok = true, state = "holding")
        } else {
            PttResult(ok = true, state = "released", flushedBytes = flushedBytesOnRelease)
        }
    }

    /**
     * Set to simulate network round-trip time. The poll cadence must not
     * depend on it: awaiting the request inside the loop made the real period
     * `interval + rtt`, which is what put translated text behind the audio.
     */
    var pollLatencyMillis: Long = 0

    override suspend fun poll(sessionId: String): PollResponse {
        pollCalls++
        if (pollLatencyMillis > 0) delay(pollLatencyMillis)
        return if (pollFrames.isEmpty()) PollResponse() else pollFrames.removeAt(0)
    }

    override suspend fun hangup(pcId: String) {
        hangups += pcId
    }

    override suspend fun consume(subject: String, sessionId: String, seconds: Int): Balance? {
        consumes += seconds
        return null
    }

    companion object {
        const val PC_A = "pc-a-3f9c1d2e"
        const val PC_B = "pc-b-7b41e0aa"
    }
}

/** Records every persisted record so double-persists are visible. */
class FakeHistoryStore : SessionHistoryStore {
    val saved = mutableListOf<SessionRecord>()

    override suspend fun save(record: SessionRecord) {
        // The real store is Room: it suspends. Without a suspension point here a
        // fake would hide any race around a guard that is checked before the
        // first `await` — which is exactly the double-`ended` bug.
        yield()
        saved += record
    }

    val endedCount: Int get() = saved.count { it.status == "ended" }
}

/** Scripted on-device pipeline. */
class FakeOfflineEngine(
    var status: PairStatus = PairStatus.INSTALLED,
    var prepareResult: Boolean = true,
) : FakeRecognitionHost(), OfflineEngine {
    var translation: String = "translated text"
    var translateThrows: Boolean = false
    var speakThrows: Boolean = false

    val spoken = mutableListOf<Pair<String, String>>()
    val translations = mutableListOf<Triple<String, String, String>>()
    var prepareCalls = 0
    val recognitionLocales = mutableListOf<String>()

    override suspend fun pairStatus(a: String, b: String): PairStatus = status

    override suspend fun prepare(a: String, b: String): Boolean {
        prepareCalls++
        return prepareResult
    }

    override suspend fun startRecognition(locale: String, onPartial: (String) -> Unit): Recognition {
        recognitionLocales += locale
        return begin(onPartial)
    }

    override suspend fun translate(text: String, from: String, to: String): String {
        translations += Triple(text, from, to)
        if (translateThrows) throw IllegalStateException("model missing")
        return translation
    }

    override suspend fun speak(text: String, locale: String) {
        if (speakThrows) throw IllegalStateException("no local voice")
        spoken += text to locale
    }
}

/** The recognition half of [FakeOfflineEngine], split out so the test can drive partials. */
open class FakeRecognitionHost {
    var finalTranscript: String = "hello there"
    var stopThrows: Boolean = false
    var cancelled = false

    private var partialSink: ((String) -> Unit)? = null

    fun begin(onPartial: (String) -> Unit): Recognition {
        partialSink = onPartial
        return object : Recognition {
            override suspend fun stop(): String {
                if (stopThrows) throw IllegalStateException("recognition failed")
                return finalTranscript
            }

            override suspend fun cancel() {
                cancelled = true
            }
        }
    }

    /** Push a partial transcript, as the on-device recogniser would. */
    fun emitPartial(text: String) = partialSink?.invoke(text)
}
