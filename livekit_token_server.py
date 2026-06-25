"""Small browser client and token service for local LiveKit agent testing."""

from __future__ import annotations

import asyncio
import audioop
import base64
import contextlib
import json
import os
import time
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from livekit import api, rtc
from loguru import logger

load_dotenv(override=True)

app = FastAPI(title="Utilities10x LiveKit Demo")

TELNYX_FRAME_MS = 20
TELNYX_SAMPLE_RATE = 8000
TELNYX_ULAW_FRAME_BYTES = TELNYX_SAMPLE_RATE * TELNYX_FRAME_MS // 1000
TELNYX_PCM_FRAME_BYTES = TELNYX_ULAW_FRAME_BYTES * 2


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _room_token(room: str, identity: str) -> str:
    return (
        api.AccessToken(
            _env("LIVEKIT_API_KEY", "devkey"),
            _env("LIVEKIT_API_SECRET", "secret"),
        )
        .with_identity(identity)
        .with_name("Utilities Demo Caller")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


def _participant_token(room: str, identity: str, name: str) -> str:
    return (
        api.AccessToken(
            _env("LIVEKIT_API_KEY", "devkey"),
            _env("LIVEKIT_API_SECRET", "secret"),
        )
        .with_identity(identity)
        .with_name(name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


def _observer_token(room: str, identity: str) -> str:
    """Read-only token for the graph/observer UI."""
    return (
        api.AccessToken(
            _env("LIVEKIT_API_KEY", "devkey"),
            _env("LIVEKIT_API_SECRET", "secret"),
        )
        .with_identity(identity)
        .with_name("Graph Observer")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=False,
                can_subscribe=True,
                can_publish_data=False,
            )
        )
        .to_jwt()
    )


async def _ensure_room_and_agent_dispatch(room: str) -> None:
    livekit_url = _env("LIVEKIT_URL", "ws://localhost:7880")
    agent_name = _env("AGENT_NAME", "utilities10x-agent")

    async with api.LiveKitAPI(
        url=livekit_url,
        api_key=_env("LIVEKIT_API_KEY", "devkey"),
        api_secret=_env("LIVEKIT_API_SECRET", "secret"),
    ) as lkapi:
        create_room = api.CreateRoomRequest()
        create_room.name = room
        try:
            await lkapi.room.create_room(create_room)
        except Exception as exc:
            logger.debug("Room creation skipped for {}: {}", room, exc)

        dispatches = await lkapi.agent_dispatch.list_dispatch(room)
        if any(dispatch.agent_name == agent_name for dispatch in dispatches):
            return

        dispatch_req = api.CreateAgentDispatchRequest()
        dispatch_req.room = room
        dispatch_req.agent_name = agent_name
        await lkapi.agent_dispatch.create_dispatch(dispatch_req)
        logger.info("Dispatched agent {} into room {}", agent_name, room)


CLIENT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Utilities10x Outage Demo</title>
  <style>
    body { font: 16px system-ui; background: #0d1117; color: #e6edf3; display: grid; min-height: 100vh; place-items: center; margin: 0; }
    main { width: min(480px, calc(100vw - 40px)); background: #161b22; border: 1px solid #30363d; border-radius: 14px; padding: 24px; }
    h1 { font-size: 20px; margin: 0 0 20px; }
    input, button { box-sizing: border-box; width: 100%; padding: 10px 12px; border-radius: 7px; font: inherit; }
    input { color: inherit; background: #0d1117; border: 1px solid #30363d; margin-bottom: 12px; }
    .buttons { display: flex; gap: 10px; }
    button { border: 0; background: #238636; color: white; cursor: pointer; }
    button.secondary { background: #30363d; }
    button:disabled { opacity: .45; cursor: default; }
    #talk { margin-top: 14px; min-height: 72px; background: #1f6feb; font-weight: 700; font-size: 18px; touch-action: none; user-select: none; }
    #talk.talking { background: #da3633; transform: scale(.99); }
    .scenario { margin: 0 0 16px; padding: 12px 14px; border: 1px solid #30363d; border-radius: 8px; background: #0d1117; color: #c9d1d9; line-height: 1.45; }
    .scenario strong { display: block; margin-bottom: 5px; color: #58a6ff; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }
    #status { color: #8b949e; min-height: 24px; margin-top: 16px; }
    #log { margin-top: 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 10px; min-height: 120px; max-height: 220px; overflow: auto; white-space: pre-wrap; font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; color: #9ecbff; }
    audio { width: 100%; margin-top: 12px; }
  </style>
</head>
<body>
<main>
  <h1>Utilities10x Outage Demo</h1>
  <div class="scenario">
    <strong>Caller script</strong>
    “My street has been without power for six hours. Why hasn’t it been fixed yet, and when will it be back?”
  </div>
  <input id="room" value="utilities10x-demo" aria-label="Room name">
  <div class="buttons">
    <button id="join">Join call</button>
    <button id="leave" class="secondary" disabled>Leave</button>
  </div>
  <button id="talk" disabled>Hold to talk</button>
  <div id="status">Ready</div>
  <div id="audio"></div>
  <pre id="log"></pre>
</main>
<script type="module">
  import { Room, RoomEvent, Track, createLocalAudioTrack } from "https://esm.sh/livekit-client@2";
  const join = document.querySelector("#join");
  const leave = document.querySelector("#leave");
  const talk = document.querySelector("#talk");
  const status = document.querySelector("#status");
  const audio = document.querySelector("#audio");
  const logEl = document.querySelector("#log");
  let activeRoom;
  let activeMicrophone;
  let isTalking = false;
  let talkOperation = Promise.resolve();

  function setTalking(enabled) {
    const microphone = activeMicrophone;
    if (!microphone || talk.disabled || enabled === isTalking) return talkOperation;
    isTalking = enabled;
    talk.classList.toggle("talking", enabled);
    talk.textContent = enabled ? "Talking — release to stop" : "Hold to talk";
    talkOperation = talkOperation.then(async () => {
      try {
        if (enabled) {
          await microphone.unmute();
          setStatus("Talking…");
          log("push-to-talk opened");
        } else {
          await microphone.mute();
          setStatus("Connected — hold the button or Space to talk");
          log("push-to-talk closed");
        }
      } catch (error) {
        log(`push-to-talk warning: ${error?.message || error}`);
      }
    });
    return talkOperation;
  }

  async function disposeCall() {
    const room = activeRoom;
    const microphone = activeMicrophone;
    activeRoom = undefined;
    activeMicrophone = undefined;
    isTalking = false;
    talk.disabled = true;
    talk.classList.remove("talking");
    talk.textContent = "Hold to talk";

    if (room && microphone) {
      try {
        await microphone.mute();
        await room.localParticipant.unpublishTrack(microphone);
      } catch (error) {
        log(`microphone unpublish warning: ${error?.message || error}`);
      }
    }
    if (microphone) {
      microphone.stop();
      log("microphone stopped");
    }
    if (room) {
      room.removeAllListeners();
      await room.disconnect();
    }
    audio.querySelectorAll("audio").forEach((element) => {
      element.pause();
      element.srcObject = null;
      element.remove();
    });
  }

  function setStatus(message) {
    status.textContent = message;
    log(message);
  }

  function log(message) {
    const ts = new Date().toLocaleTimeString("en-GB", { hour12: false });
    logEl.textContent += `[${ts}] ${message}\n`;
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function attachAudio(track, participantId) {
    const element = track.attach();
    element.autoplay = true;
    element.playsInline = true;
    element.controls = true;
    audio.appendChild(element);
    log(`audio track attached from ${participantId}`);
    try {
      if (activeRoom?.startAudio) await activeRoom.startAudio();
      await element.play();
      log(`audio playback started for ${participantId}`);
    } catch (error) {
      log(`audio playback blocked for ${participantId}: ${error?.message || error}`);
    }
  }

  join.onclick = async () => {
    try {
      join.disabled = true;
      await disposeCall();
      audio.replaceChildren();
      logEl.textContent = "";
      setStatus("Fetching room token…");
      const roomName = document.querySelector("#room").value.trim() || "utilities10x-demo";
      const response = await fetch(
        `/token?fresh=true&room=${encodeURIComponent(roomName)}`
      );
      if (!response.ok) throw new Error(`Token request failed (${response.status})`);
      const credentials = await response.json();
      activeRoom = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      activeRoom
        .on(RoomEvent.SignalConnected, () => log("signal connected"))
        .on(RoomEvent.ParticipantConnected, (participant) => log(`participant joined: ${participant.identity}`))
        .on(RoomEvent.ParticipantDisconnected, (participant) => log(`participant left: ${participant.identity}`))
        .on(RoomEvent.TrackSubscribed, (track, _, participant) => {
          log(`track subscribed: ${track.kind} from ${participant.identity}`);
          if (track.kind === Track.Kind.Audio || track.kind === "audio") {
            attachAudio(track, participant.identity);
          }
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          log(`track unsubscribed: ${track.kind}`);
          track.detach().forEach((el) => el.remove());
        })
        .on(RoomEvent.AudioPlaybackStatusChanged, () => {
          log(`audio playback canPlay=${activeRoom?.canPlaybackAudio}`);
        });
      activeRoom.on(RoomEvent.Disconnected, () => {
        setStatus("Disconnected");
        join.disabled = false;
        leave.disabled = true;
        talk.disabled = true;
      });

      setStatus(`Connecting to local LiveKit at ${credentials.url}…`);
      await activeRoom.connect(credentials.url, credentials.token);
      if (activeRoom.startAudio) await activeRoom.startAudio();

      setStatus("Requesting microphone permission…");
      const microphone = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      activeMicrophone = microphone;
      await microphone.mute();

      setStatus("Publishing muted microphone…");
      await activeRoom.localParticipant.publishTrack(microphone);
      log("microphone published in push-to-talk mode");
      leave.disabled = false;
      talk.disabled = false;
      setStatus(`Connected to ${credentials.room} — hold the button or Space to talk`);
    } catch (error) {
      await disposeCall();
      setStatus(`Error: ${error.message || error}`);
      join.disabled = false;
    }
  };

  leave.onclick = async () => {
    await disposeCall();
    join.disabled = false;
    leave.disabled = true;
    setStatus("Disconnected");
  };

  talk.addEventListener("pointerdown", async (event) => {
    if (talk.disabled) return;
    event.preventDefault();
    talk.setPointerCapture?.(event.pointerId);
    await setTalking(true);
  });

  for (const eventName of ["pointerup", "pointercancel", "lostpointercapture"]) {
    talk.addEventListener(eventName, async (event) => {
      event.preventDefault();
      await setTalking(false);
    });
  }

  window.addEventListener("keydown", async (event) => {
    if (
      event.code === "Space"
      && !event.repeat
      && document.activeElement?.tagName !== "INPUT"
      && !talk.disabled
    ) {
      event.preventDefault();
      await setTalking(true);
    }
  });

  window.addEventListener("keyup", async (event) => {
    if (event.code === "Space" && !talk.disabled) {
      event.preventDefault();
      await setTalking(false);
    }
  });

  window.addEventListener("beforeunload", () => {
    activeMicrophone?.mute();
    activeMicrophone?.stop();
    activeRoom?.disconnect();
  });
</script>
</body>
</html>"""


S2S_CLIENT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Maya — Realtime (S2S) Test</title>
  <style>
    body { font: 16px system-ui; background: #0d1117; color: #e6edf3; display: grid; min-height: 100vh; place-items: center; margin: 0; }
    main { width: min(480px, calc(100vw - 40px)); background: #161b22; border: 1px solid #30363d; border-radius: 14px; padding: 24px; }
    h1 { font-size: 20px; margin: 0 0 16px; }
    .buttons { display: flex; gap: 10px; }
    button { flex: 1; border: 0; border-radius: 8px; padding: 12px 16px; font: inherit; font-weight: 700; cursor: pointer; background: #238636; color: #fff; }
    button.secondary { background: #30363d; }
    button:disabled { opacity: .45; cursor: default; }
    #status { color: #8b949e; min-height: 24px; margin-top: 16px; }
    #log { margin-top: 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 10px; min-height: 110px; max-height: 220px; overflow: auto; white-space: pre-wrap; font: 12px ui-monospace, Menlo, monospace; color: #9ecbff; }
    #talk { width: 100%; margin-top: 12px; min-height: 64px; border: 0; border-radius: 8px; font: inherit; font-weight: 700; font-size: 17px; cursor: pointer; background: #1f6feb; color: #fff; touch-action: none; user-select: none; }
    #talk.talking { background: #da3633; transform: scale(.99); }
    #talk:disabled { opacity: .45; cursor: default; }
  </style>
</head>
<body>
<main>
  <h1>Maya — Realtime (S2S)</h1>
  <div class="buttons">
    <button id="join">Join call</button>
    <button id="leave" class="secondary" disabled>Leave</button>
  </div>
  <button id="talk" disabled>Hold to talk</button>
  <div id="status">Ready — click Join, then hold the button (or Space) to talk.</div>
  <div id="audio"></div>
  <pre id="log"></pre>
</main>
<script type="module">
  import { Room, RoomEvent, Track, createLocalAudioTrack } from "https://esm.sh/livekit-client@2";
  const joinBtn = document.querySelector("#join");
  const leaveBtn = document.querySelector("#leave");
  const talkBtn = document.querySelector("#talk");
  const statusEl = document.querySelector("#status");
  const audioEl = document.querySelector("#audio");
  const logEl = document.querySelector("#log");
  let room, mic, talking = false;
  const log = (m) => { logEl.textContent += `[${new Date().toLocaleTimeString("en-GB",{hour12:false})}] ${m}\\n`; logEl.scrollTop = logEl.scrollHeight; };
  const setStatus = (m) => { statusEl.textContent = m; log(m); };

  async function setTalking(on) {
    if (!mic || talkBtn.disabled || on === talking) return;
    talking = on;
    talkBtn.classList.toggle("talking", on);
    talkBtn.textContent = on ? "Talking — release to stop" : "Hold to talk";
    try { await (on ? mic.unmute() : mic.mute()); } catch (e) { log(`mic ${on ? "unmute" : "mute"} warn: ${e?.message || e}`); }
  }
  talkBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); talkBtn.setPointerCapture?.(e.pointerId); setTalking(true); });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) talkBtn.addEventListener(ev, (e) => { e.preventDefault(); setTalking(false); });
  window.addEventListener("keydown", (e) => { if (e.code === "Space" && !e.repeat && !talkBtn.disabled) { e.preventDefault(); setTalking(true); } });
  window.addEventListener("keyup", (e) => { if (e.code === "Space" && !talkBtn.disabled) { e.preventDefault(); setTalking(false); } });

  async function teardown() {
    talking = false;
    talkBtn.classList.remove("talking"); talkBtn.textContent = "Hold to talk"; talkBtn.disabled = true;
    try { mic?.stop(); } catch {}
    try { await room?.disconnect(); } catch {}
    audioEl.querySelectorAll("audio").forEach((el) => { el.srcObject = null; el.remove(); });
    mic = undefined; room = undefined;
  }

  joinBtn.onclick = async () => {
    joinBtn.disabled = true;
    try {
      setStatus("Getting token…");
      const res = await fetch(`/token?fresh=true&room=s2s-test`);
      const cred = await res.json();
      if (!res.ok) throw new Error(cred.error || "token failed");

      room = new Room({ adaptiveStream: true, dynacast: true });
      room.on(RoomEvent.TrackSubscribed, (track, _pub, participant) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach();
          el.autoplay = true; el.playsInline = true;
          audioEl.appendChild(el);
          el.play().catch(() => {});
          log(`agent audio attached from ${participant.identity}`);
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((e) => e.remove()));
      room.on(RoomEvent.Disconnected, () => { setStatus("Disconnected"); joinBtn.disabled = false; leaveBtn.disabled = true; });

      setStatus(`Connecting to ${cred.url}…`);
      await room.connect(cred.url, cred.token);
      if (room.startAudio) { try { await room.startAudio(); } catch {} }  // unlock autoplay (user gesture)

      mic = await createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: true });
      await mic.mute();                                // start muted — push-to-talk
      await room.localParticipant.publishTrack(mic);
      log("microphone published (push-to-talk, muted)");
      leaveBtn.disabled = false;
      talkBtn.disabled = false;
      setStatus(`Connected to ${cred.room} — hold the button or Space to talk`);
    } catch (e) {
      await teardown();
      setStatus(`Error: ${e.message || e}`);
      joinBtn.disabled = false;
    }
  };

  leaveBtn.onclick = async () => { await teardown(); joinBtn.disabled = false; leaveBtn.disabled = true; setStatus("Disconnected"); };
  window.addEventListener("beforeunload", () => { mic?.stop(); room?.disconnect(); });
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(
        S2S_CLIENT_HTML,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/graph", response_class=HTMLResponse)
@app.get("/graph/", response_class=HTMLResponse)
def graph() -> HTMLResponse:
    # The richer graph UI can be restored separately; keep the demo URL working
    # by serving the working LiveKit call page here.
    return index()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/watch")
async def watch() -> dict:
    """Find a recent active SIP room and return observer credentials.

    The graph observer uses this when watching an inbound phone call. It is safe
    for the plain call UI too; if no SIP call exists, it returns {"room": None}.
    """
    livekit_url = _env("LIVEKIT_URL", "ws://localhost:7880")
    prefix = _env("WATCH_ROOM_PREFIX", "grc-call")
    try:
        async with api.LiveKitAPI(
            url=livekit_url,
            api_key=_env("LIVEKIT_API_KEY", "devkey"),
            api_secret=_env("LIVEKIT_API_SECRET", "secret"),
        ) as lkapi:
            resp = await lkapi.room.list_rooms(api.ListRoomsRequest())
            candidate_rooms = [
                r for r in resp.rooms
                if r.name.startswith(prefix) and r.num_participants > 0
            ]
            ready_rooms = []
            for room_info in candidate_rooms:
                try:
                    participants = await lkapi.room.list_participants(
                        api.ListParticipantsRequest(room=room_info.name)
                    )
                except Exception as exc:
                    logger.debug("watch: list_participants skipped for {}: {}", room_info.name, exc)
                    continue
                identities = [
                    (participant.identity or "").lower()
                    for participant in participants.participants
                ]
                has_sip_caller = any(
                    identity.startswith("sip") or identity.startswith("telnyx")
                    for identity in identities
                )
                has_non_observer = any(
                    not identity.startswith("graph-observer") for identity in identities
                )
                if has_sip_caller and has_non_observer:
                    ready_rooms.append(room_info)
    except Exception as exc:
        logger.warning("watch: list_rooms failed: {}", exc)
        return {"room": None}

    if not ready_rooms:
        return {"room": None}
    ready_rooms.sort(key=lambda r: r.creation_time, reverse=True)
    target = ready_rooms[0].name
    identity = f"graph-observer-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    return {
        "url": _env("LIVEKIT_PUBLIC_URL") or livekit_url,
        "room": target,
        "identity": identity,
        "token": _observer_token(target, identity),
    }


@app.post("/telnyx/voice")
async def telnyx_voice(request: Request) -> dict[str, bool]:
    """Telnyx Call Control webhook that answers and streams into LiveKit."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    data = body.get("data") or {}
    event_type = data.get("event_type")
    payload = data.get("payload") or {}
    call_control_id = payload.get("call_control_id")
    direction = payload.get("direction")
    logger.info("[Telnyx] webhook event={} direction={} call_control_id={}", event_type, direction, call_control_id)

    if event_type == "call.initiated" and direction == "incoming" and call_control_id:
        host = request.headers.get("host", "")
        ws_url = f"wss://{host}/telnyx/ws"
        api_key = _env("TELNYX_API_KEY")
        if not api_key:
            logger.error("[Telnyx] TELNYX_API_KEY missing; cannot answer call")
            return {"ok": True}
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/answer",
                    json={
                        "stream_url": ws_url,
                        "stream_track": "inbound_track",
                        "stream_bidirectional_mode": "rtp",
                        "stream_bidirectional_codec": "PCMU",
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    text = await resp.text()
                    if resp.status in (200, 202):
                        logger.info("[Telnyx] answered + streaming started -> {}", ws_url)
                    else:
                        logger.error("[Telnyx] answer failed status={} body={}", resp.status, text[:500])
        except Exception as exc:
            logger.exception("[Telnyx] answer exception: {}", exc)

    return {"ok": True}


@app.get("/telnyx/voice")
async def telnyx_voice_get() -> dict[str, str]:
    return {"status": "ok", "endpoint": "telnyx voice webhook"}


@app.options("/telnyx/voice")
async def telnyx_voice_options() -> Response:
    return Response(status_code=204, headers={"Allow": "GET, POST, OPTIONS"})


async def _telnyx_send_livekit_audio(
    websocket: WebSocket,
    stream_id: str | None,
    outgoing: "asyncio.Queue[bytes | None]",
    codec: str = "PCMU",
) -> None:
    logged_first_packet = False
    use_alaw = "A" in (codec or "").upper() and "MU" not in (codec or "").upper()
    while True:
        pcm = await outgoing.get()
        if pcm is None:
            return
        if not pcm:
            continue
        encoded = audioop.lin2alaw(pcm, 2) if use_alaw else audioop.lin2ulaw(pcm, 2)
        if not logged_first_packet:
            logged_first_packet = True
            logger.info(
                "[TelnyxBridge] first outbound audio codec={} bytes_pcm={} bytes_encoded={} queue={}",
                "PCMA" if use_alaw else "PCMU",
                len(pcm),
                len(encoded),
                outgoing.qsize(),
            )
        message = {
            "event": "media",
            "media": {"payload": base64.b64encode(encoded).decode("utf-8")},
        }
        if stream_id:
            message["stream_id"] = stream_id
            message["streamId"] = stream_id
        try:
            await websocket.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError):
            return


async def _telnyx_forward_livekit_audio(
    track: rtc.Track,
    outgoing: "asyncio.Queue[bytes | None]",
    participant_identity: str,
) -> None:
    stream = rtc.AudioStream.from_track(
        track=track,
        sample_rate=TELNYX_SAMPLE_RATE,
        num_channels=1,
        frame_size_ms=TELNYX_FRAME_MS,
    )
    audio_buffer = bytearray()
    logged_first_frame = False
    async for event in stream:
        frame = event.frame
        data = memoryview(frame.data).cast("B")
        if not logged_first_frame:
            logged_first_frame = True
            logger.info(
                "[TelnyxBridge] subscribed agent audio participant={} sample_rate={} channels={} samples={} bytes={}",
                participant_identity,
                frame.sample_rate,
                frame.num_channels,
                frame.samples_per_channel,
                len(data),
            )
        audio_buffer.extend(data)
        while len(audio_buffer) >= TELNYX_PCM_FRAME_BYTES:
            chunk = bytes(audio_buffer[:TELNYX_PCM_FRAME_BYTES])
            del audio_buffer[:TELNYX_PCM_FRAME_BYTES]
            await outgoing.put(chunk)


@app.websocket("/telnyx/ws")
async def telnyx_ws(websocket: WebSocket) -> None:
    """Bridge Telnyx 8 kHz PCMU media streams into a LiveKit room."""
    await websocket.accept()
    accepted_at = time.perf_counter()
    logger.info("[TelnyxBridge] websocket accepted")

    stream_id = None
    call_control_id = None
    call_session_id = None
    codec = "PCMU"

    try:
        async for raw in websocket.iter_text():
            msg = json.loads(raw)
            event = msg.get("event")
            if event == "start":
                start = msg.get("start") or {}
                stream_id = msg.get("stream_id") or msg.get("streamId") or start.get("stream_id") or start.get("streamId")
                call_control_id = start.get("call_control_id") or start.get("callControlId") or msg.get("call_control_id")
                call_session_id = start.get("call_session_id") or start.get("callSessionId") or msg.get("call_session_id")
                media_format = start.get("media_format") or start.get("mediaFormat") or {}
                codec = str(
                    media_format.get("encoding")
                    or media_format.get("codec")
                    or msg.get("codec")
                    or "PCMU"
                ).upper()
                logger.info(
                    "[TelnyxBridge] start stream_id={} call_control_id={} call_session_id={} codec={} media_format={}",
                    stream_id,
                    call_control_id,
                    call_session_id,
                    codec,
                    media_format,
                )
                break
            if event == "stop":
                return
    except (WebSocketDisconnect, RuntimeError):
        return

    if not stream_id:
        logger.warning("[TelnyxBridge] websocket closed before start event")
        return

    room_name = f"{_env('WATCH_ROOM_PREFIX', 'grc-call')}-{call_session_id or call_control_id or stream_id}"
    identity = f"telnyx-{stream_id}"
    livekit_url = _env("LIVEKIT_URL", "ws://localhost:7880")
    room = rtc.Room()
    outgoing: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
    tasks: set[asyncio.Task] = set()
    subscribed_audio_participants: set[str] = set()

    def _spawn(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.Value("KIND_AUDIO"):
            participant_identity = getattr(participant, "identity", "")
            if participant_identity in subscribed_audio_participants:
                return
            subscribed_audio_participants.add(participant_identity)
            logger.info("[TelnyxBridge] agent audio track subscribed participant={}", participant_identity)
            _spawn(_telnyx_forward_livekit_audio(track, outgoing, participant_identity))

    try:
        await _ensure_room_and_agent_dispatch(room_name)
        await room.connect(livekit_url, _participant_token(room_name, identity, "Telnyx Caller"))
        logger.info(
            "[TelnyxBridge] room connected elapsed_ms={} room={} identity={}",
            int((time.perf_counter() - accepted_at) * 1000),
            room_name,
            identity,
        )

        source = rtc.AudioSource(TELNYX_SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("telnyx-caller-audio", source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.Value("SOURCE_MICROPHONE")
        await room.local_participant.publish_track(track, options)
        logger.info("[TelnyxBridge] caller audio published room={}", room_name)

        _spawn(_telnyx_send_livekit_audio(websocket, stream_id, outgoing, codec))

        audio_buffer = bytearray()
        logged_first_media = False
        logged_first_capture = False
        use_alaw = "A" in (codec or "").upper() and "MU" not in (codec or "").upper()
        try:
            async for raw in websocket.iter_text():
                msg = json.loads(raw)
                event = msg.get("event")
                if event == "stop":
                    break
                if event != "media":
                    continue
                payload = (msg.get("media") or {}).get("payload") or msg.get("payload")
                if not payload:
                    continue
                encoded = base64.b64decode(payload)
                pcm = audioop.alaw2lin(encoded, 2) if use_alaw else audioop.ulaw2lin(encoded, 2)
                if not logged_first_media:
                    logged_first_media = True
                    logger.info(
                        "[TelnyxBridge] first inbound media codec={} bytes_encoded={} bytes_pcm={}",
                        "PCMA" if use_alaw else "PCMU",
                        len(encoded),
                        len(pcm),
                    )
                audio_buffer.extend(pcm)
                while len(audio_buffer) >= TELNYX_PCM_FRAME_BYTES:
                    chunk = bytes(audio_buffer[:TELNYX_PCM_FRAME_BYTES])
                    del audio_buffer[:TELNYX_PCM_FRAME_BYTES]
                    frame = rtc.AudioFrame(
                        chunk,
                        sample_rate=TELNYX_SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=TELNYX_ULAW_FRAME_BYTES,
                    )
                    await source.capture_frame(frame)
                    if not logged_first_capture:
                        logged_first_capture = True
                        logger.info("[TelnyxBridge] first LiveKit capture frame bytes={}", len(chunk))
        except (WebSocketDisconnect, RuntimeError):
            pass
    finally:
        logger.info("[TelnyxBridge] disconnected stream_id={} room={}", stream_id, room_name)
        await outgoing.put(None)
        for task in list(tasks):
            task.cancel()
        for task in list(tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(Exception):
            await room.disconnect()


@app.get("/token")
async def token(
    room: str = Query("utilities10x-demo", min_length=1),
    identity: str | None = Query(None),
    fresh: bool = Query(False),
) -> dict[str, str]:
    requested_room = room
    if fresh:
        room = f"{room}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    participant = identity or f"caller-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    await _ensure_room_and_agent_dispatch(room)
    return {
        "url": _env("LIVEKIT_PUBLIC_URL") or _env("LIVEKIT_URL", "ws://localhost:7880"),
        "room": room,
        "requested_room": requested_room,
        "identity": participant,
        "token": _room_token(room, participant),
    }
