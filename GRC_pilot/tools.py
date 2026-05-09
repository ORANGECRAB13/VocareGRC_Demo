"""
tools.py — Client tool definitions for the GRC Bulky Waste voice agent.

ElevenLabs ClientTools passes a single `params` dict to each handler.
"""

import os
import re
from functools import lru_cache

import requests
from loguru import logger

import street_corrector as _sc
from database import (
    get_resident,
    create_resident,
    add_booking,
    services_left,
    update_booking,
)
import bin_zones
import grc_events as _events_mod


def book_bulky_waste(params: dict) -> str:
    name           = params.get("name", "").strip()
    phone          = _normalise_phone(params.get("phone", ""))
    address        = params.get("address", "").strip()
    preferred_date = params.get("preferred_date", "").strip()

    resident = get_resident(phone)
    is_new   = resident is None

    if is_new:
        resident = create_resident(name, phone, address)

    if services_left(phone) <= 0:
        return (
            "Your property has no Bulky Waste Collection entitlements left this year. "
            "Services used up — you'll need to arrange disposal on your own. "
            "Allocations reset on 1 January."
        )

    ref             = add_booking(phone, preferred_date)
    remaining_after = services_left(phone)

    greeting = (
        f"Registered and booked, {name}."
        if is_new
        else f"Booked, {resident['customer_name']}."
    )

    remaining_msg = (
        "That was your last collection for the year."
        if remaining_after == 0
        else f"You have {remaining_after} collection{'s' if remaining_after != 1 else ''} remaining this year."
    )

    return (
        f"{greeting} "
        f"Reference: {_spell_ref(ref)}. "
        f"Date: {preferred_date}. "
        f"Place items on the kerbside the night before only. "
        f"{remaining_msg}"
    )


def check_service_status(params: dict) -> str:
    phone    = _normalise_phone(params.get("phone", ""))
    resident = get_resident(phone)

    if resident is None:
        return "No record found for that number. Would you like to make a booking?"

    remaining = resident["services_left"]
    name      = resident["customer_name"]

    if remaining <= 0:
        return f"{name}, you have no collections remaining this year. Allocations reset 1 January."

    return f"{name}, you have {remaining} collection{'s' if remaining != 1 else ''} remaining this year."


def update_booking_date(params: dict) -> str:
    phone    = _normalise_phone(params.get("phone", ""))
    name     = params.get("name", "").strip()
    new_date = params.get("new_date", "").strip()

    if not name:
        return "Please provide your full name."
    if not new_date:
        return "Please provide the new preferred date."

    status, _slot = update_booking(phone, name, new_date)
    if status == "not_found":
        return "No record found for that number. Would you like to make a booking?"
    if status == "name_mismatch":
        return "That name doesn't match our records for this number. Please confirm your full name."
    if status == "no_booking":
        return "I couldn't find an active booking for this number. Would you like to make one?"
    if status == "change_limit":
        return "That booking has already been changed once, so it can't be changed again."

    return (
        f"Updated. New date: {new_date}. "
        "Please place items on the kerbside the night before only."
    )


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

    # Apply street name correction to catch common STT errors before geocoding
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




def get_council_events(params: dict) -> str:
    """Return upcoming GRC events, optionally filtered by a keyword query."""
    query = params.get("query", "").strip().lower()

    events = _events_mod.get_events()

    if query:
        filtered = [
            e for e in events
            if query in e["title"].lower()
            or query in e["description"].lower()
            or query in e["location"].lower()
            or query in e["cost"].lower()
        ]
        if not filtered:
            # Widen: also check partial word match in any field
            filtered = [
                e for e in events
                if any(query in str(v).lower() for v in e.values())
            ]
        events = filtered

    return _events_mod.format_events_for_tool(events)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    return digits or phone


def _spell_ref(ref: str) -> str:
    return " - ".join(ref)
