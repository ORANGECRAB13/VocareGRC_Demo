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


def _correct_address(address: str) -> str:
    """Apply STT street-name correction while preserving the leading house number."""
    _num_match = re.match(r'^(\d+)\s+(.+)$', address)
    if _num_match:
        _house, _street = _num_match.group(1), _num_match.group(2)
        corrected = _sc.correct_street(_street)
        if corrected:
            return f"{_house} {corrected[0]}"
    else:
        corrected = _sc.correct_street(address)
        if corrected:
            return corrected[0]
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
