# Georges River Council (GRC) — Unified Voice AI Agent

A fully seamless, multilingual conversational voice AI customer service agent for the Georges River Council. Built with the [ElevenLabs Conversational AI Python SDK](https://github.com/elevenlabs/elevenlabs-python) and a native Node.js geographic API. 

This agent natively speaks both **English** and **Chinese Mandarin**, securely handling multiple internal intents without ever needing to drop the user or switch connection states.

---

## Capabilities

🎙️ **Natively Multilingual** — Automatically adapts to the caller's spoken language without manual inputs.
🏢 **Development Applications (DAs)** — Instantly answers comprehensive policy questions directly from the GRC local environment plans and guidelines.
📅 **Bulky Waste Booking System** — Collects name, phone, address, and date to book collections. Tracks users in SQLite and enforces the "2-per-year" entitlement policy natively.
🗑️ **General Waste & Bin Zones** — Interfaces with a local Node.js API to cross-reference addresses against complex Google Maps point-in-polygon data to tell users their local bin collection days.

---

## Project Structure

```
grc/
├── main.py               # Entry point — runs the unified voice agent AND the background Node server natively
├── tools.py              # Active client tool logic (booking, status check, address lookup)
├── database.py           # SQLite setup and CRUD helpers for Bulky Waste
├── knowledge.py          # Bulky Waste policies and FAQ data
├── da_knowledge.py       # Development Application rules and guidelines
├── customers.db          # Auto-created SQLite DB to track callers and bookings
├── api/                  # The geographic Node.js microservice
│   ├── bin-zone-api.js   # Express server endpoint for point-in-polygon map logic
│   ├── package.json      # Node dependencies
│   └── zones/            # Polygon zone definitions for Hurstville bins
├── requirements.txt      # Python dependencies
└── README.md
```

---

## Setup & Execution

### 1. Requirements

You must have **Python 3.10+** and **Node.js (v20+)** installed.

### 2. Configure Environment

Open the main `.env` in the root folder and add your API Keys:
```env
ELEVENLABS_API_KEY=your_elevenlabs_key_here
GOOGLE_MAPS_API_KEY=your_google_maps_key
```
*(Leave `UNIFIED_AGENT_ID` blank on the first run — `main.py` will generate the config remotely and save the ID for you).*

First, install the Node dependencies for the background service:
```bash
cd api
npm install
cd ..
```

### 3. Run the Unified Agent

To activate the system, you only need to run the master python script. It will automatically spawn the required Node.js backend silently behind the scenes.

```bash
python main.py
```

Speak into your microphone. The agent will adapt to whether you ask about bins, DAs, or Bulky Waste directly, entirely driven by intelligent conversational pathways. 
Press **Ctrl-C** to end the session (which cleanly shuts down the background API!).

---

## Architecture Details

- **Database:** `customers.db` is an internal SQLite database spawned instantly. It uniquely identifies users by `phone` number to enforce bulky waste quotas seamlessly.
- **Node Backend:** The python client executes synchronous web-hooks out to `localhost:3000` under the hood when a user asks about general waste. The Node instance parses Google Geocoding objects against raw geographic polygon bounding boxes to respond with exact collection zone identities (e.g., "Tuesday Zone 1").
- **Voice Configurations:** Powered by ElevenLabs' **eleven_multilingual_v2** using voice profile `DTLT09E2cxHF0DqjKVbc`.
