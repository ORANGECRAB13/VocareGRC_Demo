"""
tools.py — Client tool definitions for the GRC voice agent.

ElevenLabs ClientTools passes a single `params` dict to each handler.
"""

from __future__ import annotations

import re
from difflib import get_close_matches

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

_STREET_TYPE_ALIASES = {
    "steet": "street",
    "stret": "street",
    "strret": "street",
    "sreet": "street",
    "rod": "road",
    "raod": "road",
    "avenu": "avenue",
    "avene": "avenue",
    "plac": "place",
}

_GRC_SUBURBS = (
    "Allawah",
    "Beverley Park",
    "Beverly Hills",
    "Blakehurst",
    "Carss Park",
    "Connells Point",
    "Hurstville",
    "Hurstville Grove",
    "Kingsgrove",
    "Kogarah",
    "Kogarah Bay",
    "Kyle Bay",
    "Lugarno",
    "Mortdale",
    "Narwee",
    "Oatley",
    "Peakhurst",
    "Peakhurst Heights",
    "Penshurst",
    "Ramsgate",
    "Riverwood",
    "Sans Souci",
    "South Hurstville",
    "Warraba",
)

_SUBURB_ALIASES = {
    "waratah": "Warraba",
    "warratah": "Warraba",
    "warboss": "Warraba",
}

_STREET_PHRASE_ALIASES = {
    "waratah street": "Warraba Street",
    "warratah street": "Warraba Street",
    "warata street": "Warraba Street",
    "warrata street": "Warraba Street",
    "waroba street": "Warraba Street",
    "warboss street": "Warraba Street",
    "warboss st": "Warraba Street",
    "war boss street": "Warraba Street",
    "war boss st": "Warraba Street",
    "war ob a street": "Warraba Street",
    "war ob a st": "Warraba Street",
    "war raba street": "Warraba Street",
    "war raba st": "Warraba Street",
}

_NUMBER_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_NUMBER_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def _words_to_house_number(words: list[str]) -> tuple[int | None, int]:
    """Parse a leading spoken house number like 'forty two' or 'one hundred five'."""
    i = 0
    total = 0
    consumed = 0

    if i < len(words) and words[i] in _NUMBER_ONES and _NUMBER_ONES[words[i]] >= 1:
        if i + 1 < len(words) and words[i + 1] == "hundred":
            total += _NUMBER_ONES[words[i]] * 100
            i += 2
            consumed = i
            if i < len(words) and words[i] == "and":
                i += 1

    if i < len(words) and words[i] in _NUMBER_TENS:
        total += _NUMBER_TENS[words[i]]
        i += 1
        consumed = i
        if i < len(words) and words[i] in _NUMBER_ONES:
            total += _NUMBER_ONES[words[i]]
            i += 1
            consumed = i
    elif i < len(words) and words[i] in _NUMBER_ONES:
        total += _NUMBER_ONES[words[i]]
        i += 1
        consumed = i

    if consumed == 0 or total == 0:
        return None, 0
    return total, consumed


def _normalize_spoken_house_number(address: str) -> str:
    """Convert a leading word-form house number to digits before address lookup."""
    tokens = address.strip().split()
    if not tokens or tokens[0][0].isdigit():
        return address

    number, consumed = _words_to_house_number([t.lower().strip(".,") for t in tokens])
    if number is None:
        return address

    normalized = " ".join([str(number)] + tokens[consumed:])
    logger.info(f"[ADDRESS] Normalized spoken house number: {address!r} → {normalized!r}")
    return normalized


def _split_street_suburb(text: str) -> tuple[str, str]:
    """Split 'Beverly Place Beverly Hills' into ('Beverly Place', 'Beverly Hills').

    Finds the last street-type token and treats everything after it as suburb.
    Returns (full_text, '') if no street type is found.
    """
    tokens = text.split()
    for i, tok in enumerate(tokens):
        cleaned = tok.lower().rstrip(".,")
        street_type = _STREET_TYPE_ALIASES.get(cleaned, cleaned)
        if street_type in _STREET_TYPE_TOKENS:
            street_tokens = tokens[: i + 1]
            street_tokens[-1] = street_type.title() if len(street_type) > 2 else street_type
            return " ".join(street_tokens), " ".join(tokens[i + 1 :])
    return text, ""


def _normalize_suburb_key(suburb: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", suburb.lower()).strip()


def _correct_suburb(suburb: str) -> str:
    """Correct STT suburb mistakes against known GRC locality names."""
    stripped = suburb.strip()
    if not stripped:
        return ""

    key = _normalize_suburb_key(stripped)
    alias = _SUBURB_ALIASES.get(key)
    if alias:
        logger.info(f"[ADDRESS] Corrected suburb alias: {suburb!r} → {alias!r}")
        return alias

    suburb_by_key = {_normalize_suburb_key(name): name for name in _GRC_SUBURBS}
    exact = suburb_by_key.get(key)
    if exact:
        return exact

    matches = get_close_matches(key, suburb_by_key.keys(), n=1, cutoff=0.78)
    if matches:
        corrected = suburb_by_key[matches[0]]
        logger.info(f"[ADDRESS] Corrected suburb fuzzy match: {suburb!r} → {corrected!r}")
        return corrected

    return stripped


def _normalize_street_alias_key(street: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", street.lower()).strip()


def _correct_street_alias(street: str) -> str | None:
    """Correct high-risk STT street aliases before generic fuzzy matching.

    Warraba Street is repeatedly transcribed as Waratah/Warata/Warrata/Waroba.
    The generic street corrector legitimately knows both Waratah Street and
    Warraba Street, so without this alias the fuzzy layer can lock onto the
    wrong real street.
    """
    key = _normalize_street_alias_key(street)
    alias = _STREET_PHRASE_ALIASES.get(key)
    if alias:
        logger.info(f"[ADDRESS] Corrected street alias: {street!r} → {alias!r}")
        return alias
    return None


def _correct_address(address: str) -> str:
    """Apply STT street-name and suburb correction for GRC address lookups."""
    address = _normalize_spoken_house_number(address)
    _num_match = re.match(r'^(\d+)\s+(.+)$', address)
    if _num_match:
        _house, _rest = _num_match.group(1), _num_match.group(2)
        _street_part, _suburb_part = _split_street_suburb(_rest)
        corrected_street = _correct_street_alias(_street_part)
        corrected = None if corrected_street else _sc.correct_street(_street_part)
        if corrected_street or corrected:
            corrected_street = corrected_street or corrected[0]
            corrected_suburb = _correct_suburb(_suburb_part)
            suffix = f" {corrected_suburb}" if corrected_suburb else ""
            return f"{_house} {corrected_street}{suffix}"
    else:
        _street_part, _suburb_part = _split_street_suburb(address)
        corrected_street = _correct_street_alias(_street_part)
        corrected = None if corrected_street else _sc.correct_street(_street_part)
        if corrected_street or corrected:
            corrected_suburb = _correct_suburb(_suburb_part)
            suffix = f" {corrected_suburb}" if corrected_suburb else ""
            return f"{corrected_street or corrected[0]}{suffix}"
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
