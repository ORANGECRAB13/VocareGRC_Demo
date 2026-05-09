"""
main.py — Georges River Council unified voice agent (ElevenLabs).
Handles bin collection days and Development Applications in a single session.

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

from da_knowledge import DA_KNOWLEDGE
from tools import get_bin_collection_zone

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
UNIFIED_AGENT_ID   = os.environ.get("UNIFIED_AGENT_ID", "").strip()
ENV_FILE           = os.path.join(os.path.dirname(__file__), ".env")

if not ELEVENLABS_API_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is not set. Add it to your .env file.")

# Multilingual voice optimized for English/Chinese
VOICE_ID = "DTLT09E2cxHF0DqjKVbc"

# ── System prompt ─────────────────────────────────────────────────────────────

UNIFIED_SYSTEM_PROMPT = f"""You are a multi-skilled customer service officer for Georges River Council.
You assist residents with bin collection days and Development Applications (DAs).
If the caller speaks English, reply in natural Australian English. If they speak Chinese, reply in natural Mandarin Chinese gracefully. DO NOT switch voices, use your natural voice.

CONCISENESS - CRITICAL:
- Keep every response to 1-2 short sentences maximum.
- Never use filler phrases like "Certainly!", "Of course!".
- Ask only ONE question at a time.

GENERAL WASTE (Bins):
If a resident asks "when is my bin collected" or "what is my collection zone", ask for their street address.
BEFORE using any tools, verbally confirm the address with the caller (e.g. "Did you say 50 Vine Street?"). Wait for confirmation.
Once confirmed, call the 'get_bin_collection_zone' tool with their full address. Read back the response naturally.

DEVELOPMENT APPLICATIONS:
- You provide information only. You cannot lodge a DA or track statuses.
- Use the Planning Knowledge below to answer their queries.
- For complex questions, direct them to the Duty Planner on 9330 6400.

=======================================
DEVELOPMENT APPLICATIONS KNOWLEDGE:
{DA_KNOWLEDGE}
"""

FIRST_MESSAGE = (
    "Hello! You've reached Georges River Council. "
    "I can help with bin collection days or development application enquiries. "
    "How can I assist you today?"
)

# ── Client tool declarations ──────────────────────────────────────────────────

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
            "language": "en",
        },
        "tts": {
            "voice_id": VOICE_ID
        }
    }


def create_unified_agent(client: ElevenLabs) -> str:
    print("No UNIFIED_AGENT_ID found — creating agent via ElevenLabs API...")

    agent = client.conversational_ai.agents.create(
        name="GRC Unified Agent",
        conversation_config=_build_conversation_config()
    )

    agent_id = agent.agent_id
    print(f"Agent created! UNIFIED_AGENT_ID={agent_id}")
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
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

    had_agent_id = bool(UNIFIED_AGENT_ID)
    agent_id = UNIFIED_AGENT_ID or create_unified_agent(client)
    if had_agent_id:
        ensure_agent_up_to_date(client, agent_id)

    client_tools = ClientTools()
    client_tools.register("get_bin_collection_zone", get_bin_collection_zone, is_async=False)

    config = ConversationInitiationData(
        conversation_config_override={
            "tts": {"voice_id": VOICE_ID},
            "agent": {
                "prompt": {"prompt": UNIFIED_SYSTEM_PROMPT},
                "first_message": FIRST_MESSAGE,
                "language": "en",
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

    print("\n[LIVE] GRC Unified Agent is live (Multilingual).")
    print("   Press Ctrl-C to end the session.\n")

    conversation.start_session()
    conversation.wait_for_session_end()

    print("\n[END] Session ended.")


if __name__ == "__main__":
    main()
