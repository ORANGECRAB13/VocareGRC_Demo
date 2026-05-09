"""
grc_wastetrack.py — Georges River Council bin collection lookup via Wastetrack.

Scrapes v2.wastetrack.net (the backend powering the GRC website's bin day checker).
Uses a persistent requests.Session so the authenticity token and cookies stay valid
across the three-step flow: GET locator → POST locator_search → POST locator_show.

Public API:
    get_bin_collection_details(address: str) -> dict
    format_voice_response(result: dict) -> str | None
"""

import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup

BASE_URL = "https://v2.wastetrack.net/self_service"
KEY      = "da1d834c-3d97-4f96-9d60-4107ef0a53e6"
TOKEN    = "86c1e1b2-f3fe-4a9f-8be6-2beb66cdb5ab"

_GET_HEADERS = {
    "Origin":     "https://www.georgesriver.nsw.gov.au",
    "Referer":    "https://www.georgesriver.nsw.gov.au/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

_POST_HEADERS = {
    **_GET_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded",
}

_COMMON_FORM = {
    "recaptcha_token": "undefined",
    "key":   KEY,
    "token": TOKEN,
    "utf8":  "\u2713",  # ✓
}


# ── Address normalisation ──────────────────────────────────────────────────────

_ONES = {
    "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,
    "eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,
    "fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,
    "nineteen":19,
}
_TENS = {
    "twenty":20,"thirty":30,"forty":40,"fifty":50,
    "sixty":60,"seventy":70,"eighty":80,"ninety":90,
}

def _words_to_number(words: list[str]) -> tuple[int | None, int]:
    """Try to parse a leading sequence of words as a house number.

    Returns (number, words_consumed) or (None, 0) if no match.
    Handles: "six" → 6, "twenty three" → 23, "one hundred and five" → 105.
    """
    i = 0
    total = 0
    consumed = 0

    # Optional hundreds: "one hundred ..."
    if i < len(words) and words[i] in _ONES and _ONES[words[i]] >= 1:
        if i + 1 < len(words) and words[i + 1] == "hundred":
            total += _ONES[words[i]] * 100
            i += 2
            consumed = i
            # Optional "and" connector
            if i < len(words) and words[i] == "and":
                i += 1

    # Tens + optional ones, or just ones
    if i < len(words) and words[i] in _TENS:
        total += _TENS[words[i]]
        i += 1
        consumed = i
        if i < len(words) and words[i] in _ONES:
            total += _ONES[words[i]]
            i += 1
            consumed = i
    elif i < len(words) and words[i] in _ONES:
        total += _ONES[words[i]]
        i += 1
        consumed = i

    if consumed == 0 or total == 0:
        return None, 0
    return total, consumed


def _normalize_address(address: str) -> str:
    """Convert a leading word-form house number to digits.

    Examples:
        "Six Gannons Avenue"       → "6 Gannons Avenue"
        "Twenty Three Oak Street"  → "23 Oak Street"
        "One Hundred Forest Road"  → "100 Forest Road"
        "50 Vine Street"           → "50 Vine Street"  (unchanged)
    """
    tokens = address.strip().split()
    if not tokens:
        return address

    # Already starts with a digit — nothing to do
    if tokens[0][0].isdigit():
        return address

    number, consumed = _words_to_number([t.lower() for t in tokens])
    if number is None:
        return address

    rest = tokens[consumed:]
    return " ".join([str(number)] + rest)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_authenticity_token(html: str) -> str | None:
    match = re.search(r'name="authenticity_token"\s+value="([^"]+)"', html)
    return match.group(1) if match else None


def _extract_site_id(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.select_one('input[name="wtss_site"]')
    return inp.get("value") if inp else None


def _parse_collection_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.select_one("h1")
    address = heading.get_text(strip=True) if heading else None

    collections = []
    for row in soup.select("table.wtss-service-locator-results tbody tr"):
        cols = row.find_all("td")
        if len(cols) < 4:
            continue
        collections.append({
            "service":  cols[1].get_text(" ", strip=True),
            "schedule": re.sub(r"\s+", " ", cols[2].get_text(" ", strip=True)),
            "next":     cols[3].get_text(" ", strip=True),
        })

    return {"address": address, "collections": collections}


# ── Public API ─────────────────────────────────────────────────────────────────

# ── Persistent session (connection pooling across calls) ───────────────────────
_SESSION = requests.Session()


def get_bin_collection_details(address: str) -> dict:
    """Look up GRC bin collection schedule for an address via Wastetrack.

    Args:
        address: Street address, e.g. "50 Vine Street Hurstville"

    Returns:
        On success: {"success": True, "address": str, "collections": [...]}
        On failure: {"success": False, "error": str, "address_query": str}
    """
    address = _normalize_address(address)

    # Step 1: GET locator page with key param → extract fresh authenticity_token + cookies.
    # Wastetrack serves a council-specific page only when the key query param is present.
    # No Content-Type on GET requests — it causes a 500.
    try:
        locator_resp = _SESSION.get(
            f"{BASE_URL}/locator",
            params={"key": KEY, "token": TOKEN},
            headers=_GET_HEADERS,
            timeout=15,
        )
        locator_resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"Could not reach Wastetrack: {e}", "address_query": address}

    auth_token = _extract_authenticity_token(locator_resp.text)
    if not auth_token:
        return {"success": False, "error": "Could not extract authenticity token", "address_query": address}

    # Step 2: POST locator_search → extract wtss_site
    try:
        search_resp = _SESSION.post(
            f"{BASE_URL}/locator_search",
            headers=_POST_HEADERS,
            data={**_COMMON_FORM, "authenticity_token": auth_token, "search": address},
            timeout=15,
        )
        search_resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"Address search failed: {e}", "address_query": address}

    # Refresh token from search response if a newer one is embedded
    new_token = _extract_authenticity_token(search_resp.text)
    if new_token:
        auth_token = new_token

    wtss_site = _extract_site_id(search_resp.text)
    if not wtss_site:
        return {"success": False, "error": "No matching address found", "address_query": address}

    # Step 3: POST locator_show → parse collection table
    try:
        show_resp = _SESSION.post(
            f"{BASE_URL}/locator_show",
            headers=_POST_HEADERS,
            data={**_COMMON_FORM, "authenticity_token": auth_token, "wtss_site": wtss_site},
            timeout=15,
        )
        show_resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"Collection detail request failed: {e}", "address_query": address}

    parsed = _parse_collection_html(show_resp.text)
    if not parsed["collections"]:
        return {"success": False, "error": "No collection data in response", "address_query": address}

    return {"success": True, **parsed}


def format_voice_response(result: dict) -> str | None:
    """Convert a get_bin_collection_details result into a natural voice string.

    Returns None if the result is unsuccessful or has no data (caller handles fallback).
    """
    if not result.get("success"):
        return None

    raw_address = result.get("address", "your address")
    # Strip suburb — keep only the street part before the last comma
    address     = raw_address.split(",")[0].strip() if "," in raw_address else raw_address

    collections = result.get("collections", [])
    if not collections:
        return None

    _ORDER = {"general waste": 0, "recycling": 1, "garden organics": 2}
    collections = sorted(collections, key=lambda c: _ORDER.get(c.get("service", "").lower().strip(), 99))

    parts = []
    for c in collections:
        service  = c.get("service", "")
        schedule = c.get("schedule", "").lower()
        next_raw = c.get("next", "")
        try:
            d        = datetime.strptime(next_raw, "%d/%m/%Y")
            next_fmt = f"{d.day} {d.strftime('%B')}"
        except ValueError:
            next_fmt = next_raw
        parts.append(f"{service} is {schedule}, next on {next_fmt}")

    return f"For {address}, {'. '.join(parts)}."


if __name__ == "__main__":
    import json
    result = get_bin_collection_details("50 Vine Street Hurstville")
    print(json.dumps(result, indent=2))
    print()
    print(format_voice_response(result))
