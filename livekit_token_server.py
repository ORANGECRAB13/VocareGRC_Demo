"""Small browser client and token service for local LiveKit agent testing."""

from __future__ import annotations

import asyncio
import os
import time
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from livekit import api
from loguru import logger

load_dotenv(override=True)

app = FastAPI(title="Utilities10x LiveKit Demo")


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
    #status { color: #8b949e; min-height: 24px; margin-top: 16px; }
    #log { margin-top: 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 10px; min-height: 120px; max-height: 220px; overflow: auto; white-space: pre-wrap; font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; color: #9ecbff; }
    audio { width: 100%; margin-top: 12px; }
  </style>
</head>
<body>
<main>
  <h1>Utilities10x Outage Demo</h1>
  <input id="room" value="utilities10x-demo" aria-label="Room name">
  <div class="buttons">
    <button id="join">Join call</button>
    <button id="leave" class="secondary" disabled>Leave</button>
  </div>
  <div id="status">Ready</div>
  <div id="audio"></div>
  <pre id="log"></pre>
</main>
<script type="module">
  import { Room, RoomEvent, Track, createLocalAudioTrack } from "https://esm.sh/livekit-client@2";
  const join = document.querySelector("#join");
  const leave = document.querySelector("#leave");
  const status = document.querySelector("#status");
  const audio = document.querySelector("#audio");
  const logEl = document.querySelector("#log");
  let activeRoom;
  let activeMicrophone;

  async function disposeCall() {
    const room = activeRoom;
    const microphone = activeMicrophone;
    activeRoom = undefined;
    activeMicrophone = undefined;

    if (room && microphone) {
      try {
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

      setStatus("Publishing microphone…");
      await activeRoom.localParticipant.publishTrack(microphone);
      log("microphone published");
      leave.disabled = false;
      setStatus(`Connected to ${credentials.room} — speak to Ava`);
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

  window.addEventListener("beforeunload", () => {
    activeMicrophone?.stop();
    activeRoom?.disconnect();
  });
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(
        CLIENT_HTML,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
