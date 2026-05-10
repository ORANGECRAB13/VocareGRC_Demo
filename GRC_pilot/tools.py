"""
tools.py — Client tool definitions for the GRC voice agent.

ElevenLabs ClientTools passes a single `params` dict to each handler.
"""

import os
import re
from functools import lru_cache

import requests
from loguru import logger

import street_corrector as _sc
import bin_zones


@lru_cache(maxsize=128)
def _geocode_address(address: str, api_key: str) -> tuple:
    """Cached geocoding. Returns (lat, lng) or (None, None)."""
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address + ", Georges River NSW", "key": api_key},
            timeout=10,
        )
        results = resp.json().get("results", [])
        if not results:
            return (None, None)
        loc = results[0]["geometry"]["location"]
        return (loc["lat"], loc["lng"])
    except Exception:
        return (None, None)


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
    """Primary bin collection lookup.

    Tries the GRC Wastetrack API first (live council data with exact next-date).
    Falls back to the local polygon zone lookup if Wastetrack is unreachable or
    returns no result.
    """
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
            logger.warning(f"[BIN TOOL] Wastetrack returned no usable data for '{address}': {result.get('error', 'empty response')} — falling back to polygon lookup")
    except Exception as e:
        logger.warning(f"[BIN TOOL] Wastetrack EXCEPTION for '{address}': {e} — falling back to polygon lookup")

    # ── Fallback: local polygon zone lookup ───────────────────────────────────
    logger.info(f"[BIN TOOL] Using polygon fallback for '{address}'")
    return get_bin_collection_zone(params)


def get_bin_collection_zone(params: dict) -> str:
    address = params.get("address", "").strip()
    if not address:
        return "Please provide your full address."

    corrected = _sc.correct_street(address)
    if corrected:
        address = corrected[0]

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return "The geocoding service is not configured. Please contact support."

    lat, lng = _geocode_address(address, api_key)
    if lat is None:
        return "I couldn't verify that exact street name. Could you please double-check and tell me the correct street name again?"

    zone = bin_zones.find_zone_for_point(lat, lng)
    if not zone:
        return "That address doesn't appear to be within a Georges River Council collection zone."

    return bin_zones.format_voice_prompt(zone)
