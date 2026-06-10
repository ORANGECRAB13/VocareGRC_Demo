# Vobiz India Phone Voice Agent Runbook

This runbook captures the working pipeline for adding an Indian phone number through Vobiz to a Pipecat voice agent, plus the mistakes we hit and should not repeat.

## Working Architecture

```text
Indian phone number
  -> Vobiz Application
  -> Answer URL webhook
  -> XML <Stream>
  -> Vobiz websocket
  -> FastAPI /vobiz/ws
  -> Pipecat voice pipeline
```

For this app:

```text
Azure Container App:
vocare-grc-bot

Resource group:
vocare-grc

Public app URL:
https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io
```

## Correct Vobiz Dashboard Setup

In Vobiz, create or edit a Voice/XML application.

Use this as the application Answer URL:

```text
https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/answer
```

Use:

```text
Method: POST
Content type: application/x-www-form-urlencoded
```

Do not put the Azure URL into a SIP URI field. The Azure URL belongs in Answer URL, Webhook URL, or equivalent call-answer callback field.

If Vobiz asks for a separate stream websocket URL, use:

```text
wss://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/ws
```

Normally, though, Vobiz should only need the Answer URL. The Answer URL returns XML that tells Vobiz which websocket to open.

## Working XML Shape

The Answer URL returns compact XML. Keep it close to the Vobiz docs example:

```xml
<?xml version="1.0" encoding="UTF-8"?><Response><Stream bidirectional="true" audioTrack="inbound" streamTimeout="7200" keepCallAlive="true" contentType="audio/x-l16;rate=16000">wss://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/ws</Stream></Response>
```

Important details:

- Use `application/xml`.
- Keep XML compact while debugging. Avoid extra whitespace inside `<Stream>...</Stream>`.
- Use `bidirectional="true"` so the bot can both receive and send audio.
- Use `audioTrack="inbound"`.
- Use `contentType="audio/x-l16;rate=16000"` for the current working Vobiz path.
- Keep `streamTimeout="7200"` and `keepCallAlive="true"`.

## Required App Routes

The bot should expose:

```text
GET/POST/OPTIONS /vobiz/answer
GET/POST/OPTIONS /answer
GET/POST/OPTIONS /telnyx/answer
GET/POST/OPTIONS /vobiz/stream-status
WS               /vobiz/ws
```

The `/answer` route is a compatibility alias for Vobiz examples.

The `/telnyx/answer` route is only a safety alias. Do not intentionally configure Vobiz to use it.

The `OPTIONS` routes matter because the Vobiz dashboard may preflight or test the Answer URL before making a real `POST`.

## Expected Azure Logs

A successful call setup should show this sequence:

```text
POST /vobiz/answer 200 OK
WebSocket /vobiz/ws [accepted]
Vobiz call started - stream_id=... call_id=... encoding=audio/x-l16 sample_rate=16000
Vobiz client connected
```

If the bot disconnects normally:

```text
Vobiz client disconnected
```

Watch logs with:

```bash
az containerapp logs show \
  --name vocare-grc-bot \
  --resource-group vocare-grc \
  --tail 200 \
  --follow
```

Filter for Vobiz:

```bash
az containerapp logs show \
  --name vocare-grc-bot \
  --resource-group vocare-grc \
  --tail 300 \
  | rg -i "vobiz|websocket|call started|client connected|encoding|sample|error|exception"
```

## Deployment Rules

Use unique image tags for deploys. Do not rely on `latest` when debugging Azure Container Apps, because the app may appear updated while the old image is still serving.

Example:

```bash
./deploy.sh --acr-build vobiz-fix-$(date +%Y%m%d%H%M%S)
```

Then confirm the live revision:

```bash
az containerapp revision list \
  --name vocare-grc-bot \
  --resource-group vocare-grc \
  --query "[].{name:name,active:properties.active,traffic:properties.trafficWeight,health:properties.healthState,running:properties.runningState}" \
  -o table
```

Confirm the Answer URL:

```bash
curl -i -X POST \
  https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/answer
```

Confirm preflight support:

```bash
curl -i -X OPTIONS \
  https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/answer
```

Expected OPTIONS response:

```text
HTTP/2 204
Allow: GET, POST, OPTIONS
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, OPTIONS
```

## Mistakes Not To Repeat

### Mistake: Using Twilio or Telnyx endpoints for Vobiz

Do not configure Vobiz to use:

```text
/twilio/voice
/twilio/ws
/telnyx/voice
/telnyx/ws
```

Vobiz uses its own Answer URL XML flow and websocket event protocol.

Use:

```text
/vobiz/answer
/vobiz/ws
```

### Mistake: Putting the Azure URL in the SIP URI field

The Azure URL is not the Vobiz SIP URI. Put it in the Answer URL or webhook field.

### Mistake: Ignoring OPTIONS

Vobiz dashboard tests may send:

```text
OPTIONS /vobiz/answer
```

If the app returns `405 Method Not Allowed`, the dashboard may report the endpoint as unreachable even though real `POST` works.

### Mistake: Reusing `latest` while debugging

Azure Container Apps may keep serving an old image if the image reference does not change. Use unique tags while debugging.

### Mistake: Adding extra XML attributes too early

Extra fields such as `statusCallbackUrl` can be useful later, but while debugging XML validity, keep the XML minimal and close to the docs example.

### Mistake: Byte-swapping L16 audio

The loud static issue was caused by treating Vobiz L16 audio as if it needed network byte order conversion. In this integration, do not byte-swap the L16 PCM unless a later test proves otherwise.

Symptoms of wrong byte order or codec mismatch:

```text
Call connects
Bot pipeline starts
Caller hears very loud static/noise instead of the agent voice
```

## Troubleshooting Tree

### Dashboard says unreachable

Check whether Azure logs show anything.

If no Azure log appears:

- Wrong URL.
- Wrong Vobiz field.
- DNS/network issue.
- Dashboard is checking a different endpoint.

If Azure shows `OPTIONS /vobiz/answer 405`:

- Add or fix OPTIONS support.

If Azure shows `POST /vobiz/answer 200 OK`:

- The Answer URL is reachable. Move to XML/websocket checks.

### Dashboard says invalid XML

First confirm the response:

```bash
curl -i -X POST https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/answer
```

The response should be:

```text
HTTP 200
content-type: application/xml
```

And the body should be a single XML document with `<Response>` as root.

If normal XML parsers accept it but Vobiz still reports invalid XML:

- Check that the URL is in the Answer URL field, not SIP URI or another URI field.
- Keep the XML compact.
- Remove optional attributes.
- Match the docs example as closely as possible.

### Answer URL works but websocket never starts

Azure logs will show:

```text
POST /vobiz/answer 200 OK
```

But will not show:

```text
WebSocket /vobiz/ws [accepted]
Vobiz call started
```

Possible causes:

- Vobiz rejected the XML after fetching it.
- Vobiz cannot reach the websocket URL.
- The websocket URL is malformed.
- The dashboard application is not actually assigned to the phone number.

### Websocket starts but no agent voice

Azure logs should show:

```text
WebSocket /vobiz/ws [accepted]
Vobiz call started
Vobiz client connected
```

Then check:

- STT provider configuration.
- LLM provider/env vars.
- TTS provider/env vars.
- Whether the bot queued the initial greeting.
- Any exceptions after `Vobiz client connected`.

### Websocket starts but caller hears static

This is almost always audio format mismatch.

Check the `Vobiz call started` log:

```text
encoding=audio/x-l16 sample_rate=16000
```

If using L16:

- Send raw PCM at 16 kHz.
- Do not byte-swap.
- Ensure outbound `playAudio` sends `contentType: audio/x-l16` and `sampleRate: 16000`.

If static continues, fallback experiment:

```xml
contentType="audio/x-mulaw;rate=8000"
```

Then update the serializer to send `audio/x-mulaw` at `8000`.

## Provider Protocol Summary

Vobiz inbound websocket events should include:

```text
start
media
stop
```

Bot outbound websocket events should include:

```text
playAudio
clearAudio
checkpoint
```

Current serializer handles:

```text
Inbound media -> InputAudioRawFrame
Outbound AudioRawFrame -> playAudio
InterruptionFrame -> clearAudio
```

## Future Voice Agent Checklist For Indian Numbers

Before building:

- Confirm the phone number is assigned to the correct Vobiz application.
- Confirm the application has the correct Answer URL.
- Confirm method is POST.
- Confirm the app exposes OPTIONS for dashboard checks.
- Confirm the Answer URL returns `application/xml`.
- Confirm the XML uses Vobiz `<Response><Stream>...</Stream></Response>`.

During first test:

- Watch Azure logs live.
- Confirm `POST /vobiz/answer 200 OK`.
- Confirm websocket accepted.
- Confirm `Vobiz call started`.
- Confirm encoding and sample rate.
- Confirm `Vobiz client connected`.

Audio validation:

- If no websocket: config/XML issue.
- If websocket but no greeting: pipeline issue.
- If greeting is static: codec/byte-order issue.
- If greeting is delayed: LLM/TTS latency or cold start.

Deployment:

- Use unique tags.
- Confirm 100% traffic on the latest healthy revision.
- Test the live Answer URL with curl.
- Test OPTIONS with curl.

## Known Working Values

```text
Answer URL:
https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/answer

WebSocket:
wss://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vobiz/ws

Content type:
application/xml

Stream contentType:
audio/x-l16;rate=16000

Vobiz stream encoding observed in logs:
audio/x-l16

Vobiz stream sample rate observed in logs:
16000
```

