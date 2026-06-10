"""
grc_wastetrack.py — Georges River Council bin collection lookup via Wastetrack.

Scrapes v2.wastetrack.net (the backend powering the GRC website's bin day checker).
Uses a persistent requests.Session so the authenticity token and cookies stay valid
across the three-step flow: GET locator → POST locator_search → POST locator_show.

Public API:
    get_bin_collection_details(address: str) -> dict
    format_voice_response(result: dict) -> str | None
"""

from __future__ import annotations

import re
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from loguru import logger

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


def _normalize_match_text(text: str) -> str:
    text = re.sub(r"\b(?:nsw|australia)\b|\b\d{4}\b", " ", text.lower())
    text = re.sub(r"\bstreet\b", "st", text)
    text = re.sub(r"\broad\b", "rd", text)
    text = re.sub(r"\bavenue\b", "ave", text)
    text = re.sub(r"\bplace\b", "pl", text)
    text = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()
    return text


def _house_number(text: str) -> str | None:
    match = re.search(r"\b\d+[a-z]?\b", text.lower())
    return match.group(0) if match else None


# Georges River Council suburbs. Multi-word names are listed so we can detect the
# longest match first ("South Hurstville" must win over "Hurstville"). Wastetrack
# candidate labels carry the suburb in uppercase, and callers usually state it, so
# a suburb mismatch is a strong signal that a same-street/same-number candidate in
# a *different* suburb is the wrong address.
_GRC_SUBURBS = [
    "south hurstville", "hurstville grove", "peakhurst heights", "connells point",
    "beverley park", "beverly hills", "kogarah bay", "carss park", "sans souci",
    "kyle bay", "allawah", "blakehurst", "carlton", "hurstville", "kingsgrove",
    "kogarah", "lugarno", "mortdale", "narwee", "oatley", "peakhurst", "penshurst",
    "riverwood",
]


def _suburb_in_text(text: str) -> str | None:
    """Return the GRC suburb mentioned in `text`, longest match first, or None."""
    normalized = f" {_normalize_match_text(_apply_address_phrase_aliases(text))} "
    for suburb in _GRC_SUBURBS:  # already ordered longest/most-specific first
        if f" {suburb} " in normalized:
            return suburb
    return None


def _candidate_score(query: str, candidate_address: str) -> float:
    query_norm = _normalize_match_text(query)
    candidate_norm = _normalize_match_text(candidate_address)
    if not query_norm or not candidate_norm:
        return 0.0

    score = SequenceMatcher(None, query_norm, candidate_norm).ratio()
    query_house = _house_number(query_norm)
    candidate_house = _house_number(candidate_norm)
    if query_house and candidate_house:
        if query_house == candidate_house:
            score += 0.25
        else:
            score -= 0.35

    # Suburb agreement dominates: a caller who names a suburb almost never wants a
    # same-named street in a different one. Only weighted when both sides name a
    # known suburb, so suburb-stripped query variants stay neutral.
    query_suburb = _suburb_in_text(query)
    candidate_suburb = _suburb_in_text(candidate_address)
    if query_suburb and candidate_suburb:
        if query_suburb == candidate_suburb:
            score += 0.3
        else:
            # Must exceed the max string-ratio (≈1.0) + house bonus (0.25) so a
            # wrong-suburb candidate can never present as a positive match.
            score -= 1.5
    return score


_ADDRESS_PHRASE_ALIASES = {
    "hurtsville": "hurstville",
    "hurst vill": "hurstville",
    "hurst vil": "hurstville",
    "pen shurst": "penshurst",
    "penhurst": "penshurst",
    "waroba": "warraba",
    "warboss": "warraba",
    "warbaugh": "warraba",
    "war ob a": "warraba",
    "war raba": "warraba",
    "war at a": "warraba",
    "warata": "warraba",
    "warrata": "warraba",
    "warratah": "warraba",
    # NB: "waratah" is intentionally NOT aliased — Waratah Street is a real GRC
    # street (Oatley, Kyle Bay). Disambiguating "Waratah"→"Warraba" must be
    # suburb-aware (follow-up), not a blanket rename.
}


def _apply_address_phrase_aliases(text: str) -> str:
    normalized = f" {re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()} "
    for phrase, replacement in _ADDRESS_PHRASE_ALIASES.items():
        normalized = normalized.replace(f" {phrase} ", f" {replacement} ")
    return re.sub(r"\s+", " ", normalized).strip()


def _collapse_short_token_windows(text: str) -> list[str]:
    tokens = text.split()
    variants = []
    for size in (2, 3, 4):
        for i in range(0, max(0, len(tokens) - size + 1)):
            window = tokens[i : i + size]
            if all(tok.isalpha() and len(tok) <= 4 for tok in window):
                collapsed = tokens[:i] + ["".join(window)] + tokens[i + size :]
                variants.append(" ".join(collapsed))
    return variants


def _generate_address_query_variants(address: str) -> list[str]:
    """Generate autocomplete queries that recover from STT token splitting."""
    normalized = _normalize_address(address)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []

    variants = []

    def add(value: str):
        value = re.sub(r"\s+", " ", value.strip())
        if value and value.lower() not in {v.lower() for v in variants}:
            variants.append(value)

    add(normalized)
    alias_normalized = _apply_address_phrase_aliases(normalized)
    add(alias_normalized)

    for base in (normalized, alias_normalized):
        for collapsed in _collapse_short_token_windows(base.lower()):
            add(collapsed)

    tokens = alias_normalized.split()
    house = tokens[0] if tokens and re.match(r"^\d+[a-z]?$", tokens[0].lower()) else ""
    street_type_indexes = [
        i for i, tok in enumerate(tokens)
        if tok.lower().strip(".,") in {
            "st", "street", "rd", "road", "ave", "avenue", "pl", "place",
            "cres", "crescent", "dr", "drive", "ct", "court", "ln", "lane",
            "pde", "parade", "cl", "close",
        }
    ]
    if street_type_indexes:
        street_end = street_type_indexes[-1]
        street_part = " ".join(tokens[: street_end + 1])
        suburb_part = " ".join(tokens[street_end + 1 :])
        add(street_part)
        if house and len(tokens) > 1:
            add(" ".join([house] + tokens[1 : street_end + 1]))
        if suburb_part:
            add(" ".join(tokens[1 : street_end + 1] + [suburb_part]))

    try:
        limit = max(1, int(os.getenv("WASTETRACK_ADDRESS_VARIANT_LIMIT", "8")))
    except ValueError:
        limit = 8
    return variants[:limit]


def _extract_site_candidates(html: str, query: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for inp in soup.select('input[name="wtss_site"]'):
        site_id = inp.get("value")
        label = soup.select_one(f'label[for="{inp.get("id")}"]') if inp.get("id") else None
        address_node = label.select_one(".wtss-site-fullstreet") if label else None
        address = re.sub(r"\s+", " ", address_node.get_text(" ", strip=True)) if address_node else ""
        if site_id and address:
            candidates.append({
                "site_id": site_id,
                "address": address,
                "score": _candidate_score(query, address),
            })
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _best_site_candidate(html: str, query: str) -> dict | None:
    candidates = _extract_site_candidates(html, query)
    if not candidates:
        return None
    best = candidates[0]
    query_house = _house_number(_normalize_match_text(query))
    best_house = _house_number(_normalize_match_text(best["address"]))
    if query_house and best_house and query_house != best_house and best["score"] < 0.8:
        logger.warning(
            f"[WASTETRACK] no confident address candidate for query={query!r}; "
            f"best={best['address']!r} score={best['score']:.2f}"
        )
        return None

    # If the caller named a suburb and the best candidate is in a different one,
    # refuse rather than confidently read out the wrong address.
    query_suburb = _suburb_in_text(query)
    best_suburb = _suburb_in_text(best["address"])
    if query_suburb and best_suburb and query_suburb != best_suburb:
        logger.warning(
            f"[WASTETRACK] suburb mismatch for query={query!r} "
            f"(wanted {query_suburb!r}); best={best['address']!r} in {best_suburb!r}"
        )
        return None
    logger.info(
        f"[WASTETRACK] best address candidate score={best['score']:.2f} "
        f"query={query!r} candidate={best['address']!r}"
    )
    return best


def get_address_candidates(address: str, limit: int = 10) -> list[dict]:
    """Return Wastetrack address candidates for a search string."""
    address = _normalize_address(address)
    timeout = _timeout()

    locator_resp = _SESSION.get(
        f"{BASE_URL}/locator",
        params={"key": KEY, "token": TOKEN},
        headers=_GET_HEADERS,
        timeout=timeout,
    )
    locator_resp.raise_for_status()
    auth_token = _extract_authenticity_token(locator_resp.text)
    if not auth_token:
        return []

    search_resp = _SESSION.post(
        f"{BASE_URL}/locator_search",
        headers=_POST_HEADERS,
        data={**_COMMON_FORM, "authenticity_token": auth_token, "search": address},
        timeout=timeout,
    )
    search_resp.raise_for_status()
    return _extract_site_candidates(search_resp.text, address)[:limit]


def get_expanded_address_candidates(address: str, limit: int = 10) -> list[dict]:
    """Return deduped candidates from several STT-tolerant autocomplete queries."""
    variants = _generate_address_query_variants(address)
    if not variants:
        return []

    try:
        workers = max(1, min(int(os.getenv("WASTETRACK_ADDRESS_VARIANT_WORKERS", "4")), len(variants)))
    except ValueError:
        workers = min(4, len(variants))

    by_key: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_query = {
            executor.submit(get_address_candidates, query, limit): query
            for query in variants
        }
        for future in as_completed(future_to_query):
            query = future_to_query[future]
            try:
                candidates = future.result()
            except Exception as e:
                logger.warning(f"[WASTETRACK] address variant failed query={query!r}: {e}")
                continue

            for rank, candidate in enumerate(candidates):
                address_label = candidate.get("address", "")
                key = candidate.get("site_id") or _normalize_match_text(address_label)
                if not key:
                    continue
                score = _candidate_score(address, address_label) + max(0.0, 0.12 - (rank * 0.02))
                existing = by_key.get(key)
                if not existing or score > existing.get("score", 0):
                    by_key[key] = {
                        **candidate,
                        "score": score,
                        "source_query": query,
                    }

    candidates = sorted(by_key.values(), key=lambda item: item.get("score", 0), reverse=True)

    # When the caller named a suburb, drop candidates from other suburbs so the
    # downstream LLM/scorer never even sees a wrong-suburb option. Variants that
    # strip the suburb otherwise surface same-street/same-number addresses
    # elsewhere in the council and win on string similarity alone.
    query_suburb = _suburb_in_text(address)
    if query_suburb:
        in_suburb = [
            c for c in candidates
            if _suburb_in_text(c.get("address", "")) in (query_suburb, None)
        ]
        dropped = len(candidates) - len(in_suburb)
        if dropped:
            logger.info(
                f"[WASTETRACK] dropped {dropped} candidate(s) outside suburb "
                f"{query_suburb!r} for query={address!r}"
            )
        candidates = in_suburb

    logger.info(
        f"[WASTETRACK] expanded address candidates query={address!r} "
        f"variants={len(variants)} candidates={len(candidates)}"
    )
    return candidates[:limit]


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


def _timeout() -> float:
    raw = os.getenv("WASTETRACK_TIMEOUT_SECS", "5").strip()
    try:
        return float(raw)
    except ValueError:
        return 5.0


def get_bin_collection_details(address: str) -> dict:
    """Look up GRC bin collection schedule for an address via Wastetrack.

    Args:
        address: Street address, e.g. "50 Vine Street Hurstville"

    Returns:
        On success: {"success": True, "address": str, "collections": [...]}
        On failure: {"success": False, "error": str, "address_query": str}
    """
    address = _normalize_address(address)
    total_t0 = time.perf_counter()
    timeout = _timeout()

    # Step 1: GET locator page with key param → extract fresh authenticity_token + cookies.
    # Wastetrack serves a council-specific page only when the key query param is present.
    # No Content-Type on GET requests — it causes a 500.
    try:
        t0 = time.perf_counter()
        locator_resp = _SESSION.get(
            f"{BASE_URL}/locator",
            params={"key": KEY, "token": TOKEN},
            headers=_GET_HEADERS,
            timeout=timeout,
        )
        locator_resp.raise_for_status()
        logger.info(f"[WASTETRACK] locator GET {((time.perf_counter() - t0) * 1000):.0f} ms")
    except Exception as e:
        logger.warning(f"[WASTETRACK] locator GET failed after {((time.perf_counter() - total_t0) * 1000):.0f} ms: {e}")
        return {"success": False, "error": f"Could not reach Wastetrack: {e}", "address_query": address}

    auth_token = _extract_authenticity_token(locator_resp.text)
    if not auth_token:
        return {"success": False, "error": "Could not extract authenticity token", "address_query": address}

    # Step 2: POST locator_search → extract wtss_site
    try:
        t0 = time.perf_counter()
        search_resp = _SESSION.post(
            f"{BASE_URL}/locator_search",
            headers=_POST_HEADERS,
            data={**_COMMON_FORM, "authenticity_token": auth_token, "search": address},
            timeout=timeout,
        )
        search_resp.raise_for_status()
        logger.info(f"[WASTETRACK] locator_search POST {((time.perf_counter() - t0) * 1000):.0f} ms")
    except Exception as e:
        logger.warning(f"[WASTETRACK] locator_search POST failed after {((time.perf_counter() - total_t0) * 1000):.0f} ms: {e}")
        return {"success": False, "error": f"Address search failed: {e}", "address_query": address}

    # Refresh token from search response if a newer one is embedded
    new_token = _extract_authenticity_token(search_resp.text)
    if new_token:
        auth_token = new_token

    best_candidate = _best_site_candidate(search_resp.text, address)
    wtss_site = best_candidate["site_id"] if best_candidate else _extract_site_id(search_resp.text)
    if not wtss_site:
        return {"success": False, "error": "No matching address found", "address_query": address}

    # Step 3: POST locator_show → parse collection table
    try:
        t0 = time.perf_counter()
        show_resp = _SESSION.post(
            f"{BASE_URL}/locator_show",
            headers=_POST_HEADERS,
            data={**_COMMON_FORM, "authenticity_token": auth_token, "wtss_site": wtss_site},
            timeout=timeout,
        )
        show_resp.raise_for_status()
        logger.info(f"[WASTETRACK] locator_show POST {((time.perf_counter() - t0) * 1000):.0f} ms")
    except Exception as e:
        logger.warning(f"[WASTETRACK] locator_show POST failed after {((time.perf_counter() - total_t0) * 1000):.0f} ms: {e}")
        return {"success": False, "error": f"Collection detail request failed: {e}", "address_query": address}

    parsed = _parse_collection_html(show_resp.text)
    if not parsed["collections"]:
        return {"success": False, "error": "No collection data in response", "address_query": address}

    logger.info(f"[WASTETRACK] total {((time.perf_counter() - total_t0) * 1000):.0f} ms")
    return {
        "success": True,
        "address_query": address,
        "matched_address": best_candidate["address"] if best_candidate else parsed.get("address"),
        **parsed,
    }


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


def _self_test() -> None:
    """Offline checks for suburb-aware matching (no network)."""
    assert _suburb_in_text("40 Warraba Street Hurstville") == "hurstville"
    assert _suburb_in_text("40 Waratah Street OATLEY") == "oatley"
    assert _suburb_in_text("12 South Hurstville Road") == "south hurstville"
    assert _suburb_in_text("40 Waratah Street") is None

    # Same street+number, wrong suburb must score well below the right suburb.
    wrong = _candidate_score("40 Waratah Street Hurstville", "40 Waratah Street OATLEY")
    right = _candidate_score("40 Waratah Street Oatley", "40 Waratah Street OATLEY")
    assert wrong < 0 < right, (wrong, right)

    # warbaugh -> warraba alias (safe); waratah must stay untouched.
    assert _apply_address_phrase_aliases("40 warbaugh street") == "40 warraba street"
    assert _apply_address_phrase_aliases("40 waratah street") == "40 waratah street"
    print("self-test OK")


if __name__ == "__main__":
    import json
    import sys

    if "--self-test" in sys.argv:
        _self_test()
        raise SystemExit(0)

    result = get_bin_collection_details("50 Vine Street Hurstville")
    print(json.dumps(result, indent=2))
    print()
    print(format_voice_response(result))
