"""
tools.py — Client tool definitions for the GRC voice agent.

ElevenLabs ClientTools passes a single `params` dict to each handler.
"""

import re

from loguru import logger

import street_corrector as _sc


_STREET_TYPE_TOKENS = {
    "street", "road", "avenue", "place", "crescent", "parade", "drive",
    "court", "lane", "close", "grove", "circuit", "highway", "boulevard",
    "terrace", "way", "path", "reserve", "rise", "walk", "square", "esplanade",
    # abbreviations
    "st", "rd", "ave", "av", "pde", "cres", "pl", "ct", "dr",
    "ln", "cl", "gr", "cct", "hwy", "blvd", "tce",
}


def _split_street_suburb(text: str) -> tuple[str, str]:
    """Split 'Beverly Place Beverly Hills' into ('Beverly Place', 'Beverly Hills').

    Finds the last street-type token and treats everything after it as suburb.
    Returns (full_text, '') if no street type is found.
    """
    tokens = text.split()
    for i, tok in enumerate(tokens):
        if tok.lower().rstrip(".,") in _STREET_TYPE_TOKENS:
            return " ".join(tokens[: i + 1]), " ".join(tokens[i + 1 :])
    return text, ""


def _correct_address(address: str) -> str:
    """Apply STT street-name correction while preserving house number and suburb."""
    _num_match = re.match(r'^(\d+)\s+(.+)$', address)
    if _num_match:
        _house, _rest = _num_match.group(1), _num_match.group(2)
        _street_part, _suburb_part = _split_street_suburb(_rest)
        corrected = _sc.correct_street(_street_part)
        if corrected:
            corrected_street = corrected[0]
            suffix = f" {_suburb_part}" if _suburb_part else ""
            return f"{_house} {corrected_street}{suffix}"
    else:
        _street_part, _suburb_part = _split_street_suburb(address)
        corrected = _sc.correct_street(_street_part)
        if corrected:
            suffix = f" {_suburb_part}" if _suburb_part else ""
            return f"{corrected[0]}{suffix}"
    return address


def get_bin_collection_details(params: dict) -> str:
    """Direct GRC Wastetrack bin collection lookup."""
    address = params.get("address", "").strip()
    if not address:
        return "Please provide your full address."

    address = _correct_address(address)

    # ── Primary: GRC Wastetrack API ───────────────────────────────────────────
    try:
        from grc_wastetrack import get_bin_collection_details as _wt, format_voice_response
        result = _wt(address)
        voice  = format_voice_response(result)
        if voice:
            logger.info(f"[BIN TOOL] Wastetrack SUCCESS for '{address}' → {result.get('address')}")
            return voice
        else:
            logger.warning(f"[BIN TOOL] Wastetrack returned no usable data for '{address}': {result.get('error', 'empty response')}")
    except Exception as e:
        logger.warning(f"[BIN TOOL] Wastetrack EXCEPTION for '{address}': {e}")

    return (
        "I couldn't find a bin collection record for that address in the council bin lookup. "
        "Could you please repeat the full street address?"
    )
