#!/usr/bin/env python3
"""Provision the ElevenLabs Agents used by the agent-powered live translation path.

One agent per *output* language, not one agent for both directions. ElevenLabs validates
the TTS model against the agent's language ("English Agents must use turbo or flash v2",
while other languages need the multilingual v2.5 models) and `model_id` is not an
overridable field — so a single agent cannot legally speak both English and Mandarin.
Baking the output language in also means the runtime never has to override `language`,
removing the risk of a session silently speaking the wrong one.

Overrides are disabled by default on a new agent and are silently ignored when not enabled,
which would leave every session using the baked-in prompt and voice. This script enables
exactly the fields the translation path overrides.

Usage:
    python create_translation_agent.py            # create or update every target language
    python create_translation_agent.py --show     # print current agent configs and exit
    python create_translation_agent.py --lang zh  # only that target language
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv, set_key
from elevenlabs import ElevenLabs

from translation_agent_prompt import language_name, translation_prompt

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# Per output language: the env var holding its agent id, and a TTS model the API will
# accept for that language. English rejects the v2.5 models; the rest require them.
TARGETS = {
    "en": {
        "env": "ELEVENLABS_TRANSLATE_AGENT_EN",
        "name": "Vocare Translator → English",
        "tts_model": "eleven_flash_v2",
        "voice_env": "ELEVENLABS_VOICE_ID",
    },
    "zh": {
        "env": "ELEVENLABS_TRANSLATE_AGENT_ZH",
        "name": "Vocare Translator → Mandarin",
        "tts_model": "eleven_flash_v2_5",
        "voice_env": "ELEVENLABS_CHINESE_VOICE",
    },
}

# Which override fields the runtime sets per session. Anything false here is ignored at
# connect time, so this must stay in sync with the overrides dict in the bridge.
OVERRIDES = {
    "conversation_config_override": {
        "agent": {
            "prompt": {"prompt": True},
            "first_message": True,
        },
        "tts": {
            "voice_id": True,
            "speed": True,
            "stability": True,
            "similarity_boost": True,
        },
    }
}


def build_conversation_config(target: str) -> dict:
    """Baseline config for the agent that *speaks* `target`.

    The prompt's source language is a placeholder — the runtime overrides it with the
    actual counterpart language once both participants have joined.
    """
    spec = TARGETS[target]
    placeholder_source = "Mandarin Chinese" if target == "en" else "English"
    return {
        "agent": {
            "prompt": {
                "prompt": translation_prompt(placeholder_source, language_name(target)),
                "llm": os.getenv("ELEVENLABS_AGENT_LLM", "glm-45-air-fp8"),
                "temperature": float(os.getenv("ELEVENLABS_AGENT_TEMPERATURE", "0.1")),
            },
            # An interpreter must never speak first — it has nothing to translate yet.
            "first_message": "",
            "language": target,
        },
        "tts": {
            "voice_id": os.getenv(spec["voice_env"], "") or os.getenv("ELEVENLABS_VOICE_ID", ""),
            "model_id": spec["tts_model"],
            # The SDK AudioInterface contract is 16-bit PCM mono @ 16kHz; matching it here
            # keeps the relay free of resampling.
            "agent_output_audio_format": "pcm_16000",
            "optimize_streaming_latency": 3,
        },
        "asr": {
            "user_input_audio_format": "pcm_16000",
        },
        "turn": {
            # Interpreting starts only once the speaker actually stops.
            "turn_timeout": 7,
        },
    }


def provision(client: ElevenLabs, target: str) -> str:
    spec = TARGETS[target]
    config = build_conversation_config(target)
    agent_id = os.getenv(spec["env"], "").strip()
    llm = config["agent"]["prompt"]["llm"]

    if agent_id:
        print(f"[{target}] updating {agent_id} (llm={llm}, tts={spec['tts_model']})…")
        client.conversational_ai.agents.update(
            agent_id=agent_id,
            name=spec["name"],
            conversation_config=config,
            platform_settings={"overrides": OVERRIDES},
        )
        print(f"[{target}] updated")
        return agent_id

    print(f"[{target}] creating (llm={llm}, tts={spec['tts_model']})…")
    agent = client.conversational_ai.agents.create(
        name=spec["name"],
        conversation_config=config,
        platform_settings={"overrides": OVERRIDES},
    )
    agent_id = agent.agent_id
    print(f"[{target}] created {agent_id}")
    if os.path.exists(ENV_FILE):
        set_key(ENV_FILE, spec["env"], agent_id)
        print(f"[{target}] saved {spec['env']} to .env")
    else:
        print(f"[{target}] add to your .env: {spec['env']}={agent_id}")
    return agent_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="print current configs and exit")
    parser.add_argument("--lang", choices=sorted(TARGETS), help="only this target language")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY is not set in .env", file=sys.stderr)
        return 1

    client = ElevenLabs(api_key=api_key)
    targets = [args.lang] if args.lang else sorted(TARGETS)

    if args.show:
        for target in targets:
            agent_id = os.getenv(TARGETS[target]["env"], "").strip()
            if not agent_id:
                print(f"[{target}] {TARGETS[target]['env']} is not set")
                continue
            print(f"--- {target} ({agent_id}) ---")
            print(client.conversational_ai.agents.get(agent_id=agent_id))
        return 0

    for target in targets:
        provision(client, target)

    print("\nSet TRANSLATION_ENGINE=agent in .env to route live translation through them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
