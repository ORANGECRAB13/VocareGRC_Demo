"""
main.py — Seamless Georges River Council unified voice agent.
Handles General Waste, Bulky Waste, and Development Applications in a single seamless session using a Multilingual voice.

Usage:
    python main.py
"""

import os
import signal
import sys

from dotenv import load_dotenv, set_key
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    Conversation,
    ClientTools,
    ConversationInitiationData,
)
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

from database import init_db
from knowledge import BULKY_WASTE_KNOWLEDGE
from da_knowledge import DA_KNOWLEDGE

from tools import (
    book_bulky_waste, 
    check_service_status, 
    update_booking_date,
    get_bin_collection_zone
)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
UNIFIED_AGENT_ID   = os.environ.get("UNIFIED_AGENT_ID", "").strip()
ENV_FILE           = os.path.join(os.path.dirname(__file__), ".env")

if not ELEVENLABS_API_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is not set. Add it to your .env file.")

# Multilingual voice optimized for English/Chinese
VOICE_ID = "DTLT09E2cxHF0DqjKVbc"

# ── System prompts ───────────────────────────────────────────────────────────

_BOOKING_RULES = """
GENERAL WASTE (Bins):
If a resident asks "when is my bin collected" or "what is my collection zone", ask for their street name only (the suburb is always Hurstville).
BEFORE using any tools, you must verbally confirm the street name with the caller (e.g. "Did you say Vine Street?"). Wait for their confirmation.
Once they confirm 'yes', THEN call the 'get_bin_collection_zone' tool with their street name and Hurstville as the address (e.g. "123 Main St, Hurstville"). Read back the response naturally.

BULKY WASTE BOOKING FLOW:
When a resident wants to book a Bulky Waste Collection you MUST:
  1. Ask for their full name (if not already known).
  2. Ask for their phone number (unique identifier in the system).
  3. Ask for their property address within Georges River LGA.
  4. Ask for their preferred collection date.
  5. THEN call the book_bulky_waste tool.

SERVICE STATUS (Bulky Waste):
If a resident asks how many bulky waste collections they have left, call check_service_status.

BOOKING CHANGES (Bulky Waste):
If a resident wants to change their bulky waste booking date, call update_booking_date.
"""

_DA_RULES = """
DEVELOPMENT APPLICATIONS:
- You provide information only. You cannot lodge a DA or track statuses.
- Use the Planning Knowledge below to answer their queries.
- For complex questions, direct them to the Duty Planner on 9330 6400.
"""

# The prompt dynamically handles the user's language without needing a "detect -> reconnect" cycle.
UNIFIED_SYSTEM_PROMPT = f"""You are a multi-skilled customer service officer for Georges River Council.
You assist residents with General Waste (Bin Zones), Bulky Waste Bookings, and Development Applications (DAs).
If the caller speaks English, reply in natural Australian English. If they speak Chinese, reply in natural Mandarin Chinese gracefully. DO NOT switch voices, use your natural voice.

CONCISENESS - CRITICAL:
- Keep every response to 1-2 short sentences maximum.
- Never use filler phrases like "Certainly!", "Of course!".
- Ask only ONE question at a time.

{_BOOKING_RULES}

{_DA_RULES}

=======================================
BULKY WASTE KNOWLEDGE:
{BULKY_WASTE_KNOWLEDGE}
=======================================
DEVELOPMENT APPLICATIONS KNOWLEDGE:
{DA_KNOWLEDGE}
"""

FIRST_MESSAGE = (
    "Hello! You've reached Georges River Council. "
    "I can help with bin collection days, bulky waste bookings, or development applications. "
    "How can I assist you today?"
)

# ── Client tool declarations ─────────────────────────────────────────────────

CLIENT_TOOL_DECLARATIONS = [
    {
        "name": "get_bin_collection_zone",
        "description": "Get the general waste bin collection zone and schedule for an address.",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Full property address within Georges River LGA"}
            },
            "required": ["address"],
        },
    },
    {
        "name": "book_bulky_waste",
        "description": "Book a Bulky Waste Collection. Call this after collecting name, phone, address, and date.",
        "parameters": {
            "type": "object",
            "properties": {
                "name":           {"type": "string", "description": "Resident's full name"},
                "phone":          {"type": "string", "description": "Resident's contact phone number"},
                "address":        {"type": "string", "description": "Full property address"},
                "preferred_date": {"type": "string", "description": "Preferred collection date"},
            },
            "required": ["name", "phone", "address", "preferred_date"],
        },
    },
    {
        "name": "check_service_status",
        "description": "Check how many Bulky Waste Collection entitlements a resident has remaining.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Resident's contact phone number"},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "update_booking_date",
        "description": "Change the preferred date for an existing bulky waste booking.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone":    {"type": "string", "description": "Resident's contact phone number"},
                "name":     {"type": "string", "description": "Resident's full name"},
                "new_date": {"type": "string", "description": "New preferred collection date"},
            },
            "required": ["phone", "name", "new_date"],
        },
    },
]


def _build_conversation_config() -> dict:
    return {
        "agent": {
            "prompt": {
                "prompt": UNIFIED_SYSTEM_PROMPT,
                "tools": [
                    {
                        "type": "client",
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                        "expects_response": True,
                    }
                    for t in CLIENT_TOOL_DECLARATIONS
                ],
            },
            "first_message": FIRST_MESSAGE,
            "language": "en",  # Base config, multilingual voice handles mid-stream
        },
        "tts": {
            "voice_id": VOICE_ID
        }
    }


def create_unified_agent(client: ElevenLabs) -> str:
    print("No UNIFIED_AGENT_ID found — creating single monolithic agent via ElevenLabs API...")
    
    agent = client.conversational_ai.agents.create(
        name="GRC Seamless Unified Agent",
        conversation_config=_build_conversation_config()
    )
    
    agent_id = agent.agent_id
    print(f"✅ Unified Agent created! UNIFIED_AGENT_ID={agent_id}")
    if os.path.exists(ENV_FILE):
        set_key(ENV_FILE, "UNIFIED_AGENT_ID", agent_id)
        print(f"   Saved to {ENV_FILE}")
    else:
        print(f"   Add to your .env: UNIFIED_AGENT_ID={agent_id}")
    return agent_id


def ensure_agent_up_to_date(client: ElevenLabs, agent_id: str) -> None:
    agents = getattr(client.conversational_ai, "agents", None)
    if not agents or not hasattr(agents, "update"):
        return

    try:
        agents.update(
            agent_id=agent_id,
            conversation_config=_build_conversation_config(),
            platform_settings={
                "overrides": {
                    "conversation_config_override": {
                        "tts": {"voice_id": True},
                        "agent": {
                            "prompt": {"prompt": True},
                            "first_message": True,
                            "language": True,
                        }
                    }
                }
            }
        )
    except Exception as exc:
        print(f"Warning: unable to update agent config: {exc}")


def main() -> None:
    init_db()
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    
    had_agent_id = bool(UNIFIED_AGENT_ID)
    agent_id = UNIFIED_AGENT_ID or create_unified_agent(client)
    if had_agent_id:
        ensure_agent_up_to_date(client, agent_id)

    client_tools = ClientTools()
    client_tools.register("book_bulky_waste",        book_bulky_waste,        is_async=False)
    client_tools.register("check_service_status",    check_service_status,    is_async=False)
    client_tools.register("update_booking_date",     update_booking_date,     is_async=False)
    client_tools.register("get_bin_collection_zone", get_bin_collection_zone, is_async=False)

    config = ConversationInitiationData(
        conversation_config_override={
            "tts": {"voice_id": VOICE_ID},
            "agent": {
                "prompt": {"prompt": UNIFIED_SYSTEM_PROMPT},
                "first_message": FIRST_MESSAGE,
                "language": "en" # Multilingual voice will dynamically adapt if user speaks CN
            },
        }
    )

    conversation = Conversation(
        client=client,
        agent_id=agent_id,
        requires_auth=False,
        audio_interface=DefaultAudioInterface(),
        client_tools=client_tools,
        config=config,
        callback_agent_response=lambda text: print(f"\n[AGENT] {text}"),
        callback_user_transcript=lambda text: print(f"[USER]  {text}"),
    )

    signal.signal(signal.SIGINT, lambda *_: conversation.end_session())

    print("\n[LIVE] GRC Seamless Unified Agent is live (Multilingual).")
    print("   Press Ctrl-C to end the session.\n")

    conversation.start_session()
    conversation.wait_for_session_end()

    print("\n[END] Session ended.")


if __name__ == "__main__":
    main()
