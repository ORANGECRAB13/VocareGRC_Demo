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

# Any street-type token (abbreviation or full word) → canonical Title-case form.
# Used to preserve the street type the caller actually said: the local street
# list is incomplete (e.g. it has "Allambee Street" but not the real "Allambee
# Crescent"), so name correction must not silently rewrite the spoken type — the
# GRC API is the final validator of whether the full address exists.
_STREET_TYPE_FULL = {
    "st": "Street", "street": "Street",
    "rd": "Road", "road": "Road",
    "ave": "Avenue", "av": "Avenue", "avenue": "Avenue",
    "pde": "Parade", "parade": "Parade",
    "cres": "Crescent", "cr": "Crescent", "crescent": "Crescent",
    "pl": "Place", "place": "Place",
    "ct": "Court", "court": "Court",
    "dr": "Drive", "drive": "Drive",
    "ln": "Lane", "lane": "Lane",
    "cl": "Close", "close": "Close",
    "gr": "Grove", "grove": "Grove",
    "cct": "Circuit", "circuit": "Circuit",
    "hwy": "Highway", "highway": "Highway",
    "blvd": "Boulevard", "boulevard": "Boulevard",
    "tce": "Terrace", "terrace": "Terrace",
    "way": "Way", "path": "Path", "reserve": "Reserve",
    "rise": "Rise", "walk": "Walk", "square": "Square", "esplanade": "Esplanade",
}


def _spoken_street_type(street_part: str) -> str | None:
    """Return the canonical Title-case street type the caller said, if any."""
    tokens = street_part.split()
    if not tokens:
        return None
    last = tokens[-1].lower().strip(".,")
    last = _STREET_TYPE_ALIASES.get(last, last)
    return _STREET_TYPE_FULL.get(last)


def _preserve_spoken_street_type(corrected_street: str, spoken_type: str | None) -> str:
    """Restore the caller's spoken street type if name correction changed it.

    The corrector matches primarily on the street *name*, so it can return a
    canonical entry whose type differs from what the caller said (the dataset may
    only contain one type for that name). Trust the spoken type — if the resulting
    address doesn't exist, the API simply returns no match and we re-ask.
    """
    if not spoken_type:
        return corrected_street
    tokens = corrected_street.split()
    if len(tokens) < 2:
        return corrected_street
    last = _STREET_TYPE_ALIASES.get(tokens[-1].lower(), tokens[-1].lower())
    if last in _STREET_TYPE_FULL and _STREET_TYPE_FULL[last] != spoken_type:
        logger.info(
            f"[ADDRESS] Preserving spoken street type: "
            f"{corrected_street!r} → {' '.join(tokens[:-1] + [spoken_type])!r}"
        )
        tokens[-1] = spoken_type
        return " ".join(tokens)
    return corrected_street

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

    # Seeded from real captured STT sessions (address QA). Each key was verified
    # NOT to be a real GRC street itself, so the alias can't shadow a valid name.
    "fipp street": "Phipps Street",
    "flip street": "Phipps Street",
    "pamir street": "Premier Street",
    "almiston street": "Palmerston Street",
    "baku street": "Barcoo Street",
    "barku street": "Barcoo Street",
    "kagara street": "Coogarah Street",
    "kugera street": "Coogarah Street",
    "ugera street": "Coogarah Street",
    "naui avenue": "Narwee Avenue",
    "relay street": "Riley Street",
    "alpine avenue": "Hillpine Avenue",
    "mill pine avenue": "Hillpine Avenue",
    "pentice avenue": "Penshurst Avenue",
    "keith road": "Heath Road",
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


_LEADING_FILLER_RE = re.compile(
    r"^(?:i['’]?m in the|i am in the|it['’]?s|this is|the|uh+|um+|er+|so|and|like|"
    r"okay|ok|yeah|yep|well)\s+",
    re.IGNORECASE,
)


def _has_street_type(text: str) -> bool:
    return any(tok.lower().strip(".,") in _STREET_TYPE_TOKENS for tok in text.split())


def clean_spoken_variation(text: str) -> str | None:
    """Tidy a raw STT capture into a usable street utterance, or None to drop it.

    Handles the messy reality of browser/agent STT segments: repeated phrases
    ("Finch Place. Finch Place..."), leading filler ("I'm in the East Street"),
    interjections before a comma ("Weenie, Palmerston Street"), bare street-type
    fragments ("Street"), single stray words, and non-Latin noise.
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t or not re.search(r"[a-zA-Z]", t):
        return None

    # Collapse repeated/garbled sentence segments; prefer one that names a street type.
    segments = [s.strip() for s in re.split(r"[.;]", t) if s.strip()]
    if len(segments) > 1:
        unique = list(dict.fromkeys(segments))
        typed = [s for s in unique if _has_street_type(s)]
        t = typed[-1] if typed else max(unique, key=len)

    # Drop a leading interjection before a comma ("weenie, palmerston street").
    if "," in t:
        tail = t.split(",")[-1].strip()
        if tail:
            t = tail

    # Strip leading filler words, repeatedly ("um the ...").
    prev = None
    while prev != t:
        prev = t
        t = _LEADING_FILLER_RE.sub("", t).strip()

    tokens = t.split()
    # A usable variation is at least a name + something; a lone word (bare type,
    # stray "New"/"So", or an un-typed single name) isn't actionable.
    if len(tokens) < 2:
        return None
    # All-street-type with no actual name is noise.
    if all(tok.lower().strip(".,") in _STREET_TYPE_TOKENS for tok in tokens):
        return None
    return t


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


# Spoken/STT renderings of a street *name* (no street type) → canonical name.
# Handles cases where STT splits or mangles a name into multiple tokens, which the
# type-bearing phrase aliases above can't catch. Keyed on the lowercased, punctuation-
# stripped name only, so it applies regardless of the street type the caller used.
_STREET_NAME_ALIAS_SPOKEN = {
    "allambee": "Allambee", "alambee": "Allambee", "alanbee": "Allambee",
    "alanby": "Allambee", "alan by": "Allambee", "alan b": "Allambee",
    "alan bee": "Allambee", "alam bee": "Allambee", "alam b": "Allambee",
    "allam bee": "Allambee", "allam b": "Allambee", "all em bee": "Allambee",
    "allem bee": "Allambee", "alem bee": "Allambee", "alembee": "Allambee",
    "alan me": "Allambee", "alarm bee": "Allambee",
    # Gloucester (e.g. Gloucester Road) — STT renders the "-cester" as "sister".
    "glow sister": "Gloucester", "grow sister": "Gloucester",
    "crow sister": "Gloucester", "glass sister": "Gloucester",
    "gloss sister": "Gloucester", "glaw sister": "Gloucester",
    "gloucester": "Gloucester",
}


def _apply_street_name_aliases(street_part: str) -> str:
    """Rewrite a known spoken street-name fragment to its canonical name.

    Splits off a trailing street type (if present), normalises the name portion,
    and looks it up — so 'Alam Bee Crescent', 'Alanby', 'All em bee Street' all
    become 'Allambee …' before fuzzy matching, regardless of the spoken type.
    """
    tokens = street_part.split()
    if not tokens:
        return street_part
    last = _STREET_TYPE_ALIASES.get(tokens[-1].lower().strip(".,"), tokens[-1].lower().strip(".,"))
    has_type = len(tokens) > 1 and last in _STREET_TYPE_TOKENS
    name_tokens = tokens[:-1] if has_type else tokens
    type_token = tokens[-1] if has_type else ""
    name_key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", " ".join(name_tokens).lower())).strip()
    canonical = _STREET_NAME_ALIAS_SPOKEN.get(name_key)
    if not canonical:
        return street_part
    rebuilt = f"{canonical} {type_token}".strip()
    logger.info(f"[ADDRESS] Street name alias: {street_part!r} → {rebuilt!r}")
    return rebuilt


def _resolve_street_part(street_part: str) -> str | None:
    """Resolve a street name+type to canonical form, or None if nothing matched.

    Order: explicit spoken-name alias -> phrase alias -> fuzzy corrector. A name
    alias is authoritative — if it fires, its canonical name is used even when the
    fuzzy layer can't add a type (e.g. a bare 'Alanby' -> 'Allambee').
    """
    aliased = _apply_street_name_aliases(street_part)
    name_alias_fired = aliased != street_part

    corrected_street = _correct_street_alias(aliased)
    corrected = None if corrected_street else _sc.correct_street(aliased)
    if corrected_street or corrected:
        chosen = corrected_street or corrected[0]
        return _preserve_spoken_street_type(chosen, _spoken_street_type(aliased))
    if name_alias_fired:
        return aliased
    return None


def _correct_address(address: str) -> str:
    """Apply STT street-name and suburb correction for GRC address lookups."""
    address = _normalize_spoken_house_number(address)
    _num_match = re.match(r'^(\d+)\s+(.+)$', address)
    if _num_match:
        _house, _rest = _num_match.group(1), _num_match.group(2)
        _street_part, _suburb_part = _split_street_suburb(_rest)
        chosen = _resolve_street_part(_street_part)
        if chosen:
            corrected_suburb = _correct_suburb(_suburb_part)
            suffix = f" {corrected_suburb}" if corrected_suburb else ""
            return f"{_house} {chosen}{suffix}"
    else:
        _street_part, _suburb_part = _split_street_suburb(address)
        chosen = _resolve_street_part(_street_part)
        if chosen:
            corrected_suburb = _correct_suburb(_suburb_part)
            suffix = f" {corrected_suburb}" if corrected_suburb else ""
            return f"{chosen}{suffix}"
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


def _self_test() -> None:
    """Offline checks for street-type preservation (uses local street list only)."""
    cases = [
        # A correctly-spoken real address must NOT be rewritten to a type that
        # only exists in the local list (the bug: Crescent -> Street).
        ("8 Allambee Crescent Beverly Hills", "8 Allambee Crescent Beverly Hills"),
        # Name STT error gets corrected, but the spoken type is preserved.
        ("8 Allamby Crescent Beverly Hills", "8 Allambee Crescent Beverly Hills"),
        # Genuine Street stays Street.
        ("8 Allambee Street Beverly Hills", "8 Allambee Street Beverly Hills"),
        # Abbreviated spoken type is preserved (and expanded).
        ("8 Allambee Cres Beverly Hills", "8 Allambee Crescent Beverly Hills"),
    ]
    for raw, expected in cases:
        got = _correct_address(raw)
        assert got == expected, f"{raw!r}: expected {expected!r}, got {got!r}"
    print("tools self-test OK")


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        _self_test()
        raise SystemExit(0)
