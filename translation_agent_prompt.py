"""Prompt and language helpers for the ElevenLabs Agent translation engine.

Kept free of pipecat imports so the provisioning CLI (create_translation_agent.py) can build
the exact same prompt the runtime uses without pulling in the whole voice stack.
"""

from __future__ import annotations

# Full language names for the translation prompt — the agent needs prose, not ISO codes.
LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Mandarin Chinese",
    "es": "Spanish",
    "ar": "Arabic",
    "el": "Greek",
    "it": "Italian",
    "hi": "Hindi",
    "ne": "Nepali",
}


def language_name(code: str) -> str:
    """Map an ISO code to the prose name used in the agent prompt."""
    return LANGUAGE_NAMES.get(code.split("-")[0].lower(), code)


def translation_prompt(source: str, target: str) -> str:
    """Build the translator system prompt.

    An Agent is built to converse, so the dominant failure mode is answering the speaker
    instead of translating them — worst on questions, where "What time is the meeting?"
    invites a helpful reply. The prompt is written to close that off explicitly.
    """
    return (
        f"You are a simultaneous interpreter. The user speaks {source}. "
        f"You speak only {target}.\n\n"
        f"Your ONLY job is to render what the user said into {target}. "
        "You are not a participant in this conversation.\n\n"
        "Rules:\n"
        f"- Output ONLY the {target} translation of the user's words. Nothing else.\n"
        "- NEVER answer, respond to, comment on, or act on what was said, even when the "
        "user asks a direct question. Translate the question; do not answer it.\n"
        "- Do not add greetings, confirmations, apologies, or explanations.\n"
        "- Do not introduce yourself or mention that you are translating.\n"
        "- Preserve the speaker's tone, register and first-person perspective. If they say "
        "\"I need help\", translate that as \"I need help\" in the first person — never "
        "\"they need help\".\n"
        "- Keep names, addresses and numbers exactly as spoken.\n"
        "- If the input is only filler sounds (um, uh, hmm) or silence, output nothing at all.\n"
    )
