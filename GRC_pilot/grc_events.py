"""
grc_events.py — Georges River Council What's On events via RSS feed.

Fetches and caches the GRC events RSS feed (refreshed every 30 mins),
parses each item into a structured dict, and provides a query function
for the voice agent tool.

Feed URL: https://www.georgesriver.nsw.gov.au/Whats-On?rss=Whats-on

Custom RSS fields used (non-standard extensions):
  <startDate>  — "Sat, 05 Dec 2026 08:00:00 AEST"
  <endDate>    — same format (year 0001 = no end time)
  <location>   — venue name (may contain HTML entity artifacts)
  <bodytext>   — full HTML body (used to extract cost + booking link)
  <description>— short plain-text summary
"""

import re
import time
import threading
import datetime as dt
from email.utils import parsedate
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from loguru import logger

# ── Constants ──────────────────────────────────────────────────────────────────

RSS_URL   = "https://www.georgesriver.nsw.gov.au/Whats-On?rss=Whats-on"
EVENTS_URL = "https://www.georgesriver.nsw.gov.au/Whats-On"
CACHE_TTL = 30 * 60   # 30 minutes

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ── Cache ──────────────────────────────────────────────────────────────────────

_CACHE: list[dict] = []
_CACHE_TS: float = 0.0
_CACHE_LOCK = threading.Lock()

# ── Helpers ────────────────────────────────────────────────────────────────────

# GRC's RSS encoder drops the leading "&" from HTML entities in text fields:
# "andnbsp;" → " ", "andamp;" → "&", "andrsquo;" → "'"
_ENTITY_RE = re.compile(r'and(nbsp|amp|lsquo|rsquo|ldquo|rdquo|ndash|mdash|hellip);')
_ENTITY_MAP = {
    "nbsp": " ", "amp": "&",
    "lsquo": "'", "rsquo": "'",
    "ldquo": '"', "rdquo": '"',
    "ndash": "-", "mdash": "-",
    "hellip": "...",
}


_NUMERIC_ENTITY_RE = re.compile(r'and#(\d+);')


def _clean_entity_artifacts(text: str) -> str:
    """Fix broken HTML entity artifacts in GRC RSS text fields."""
    text = _ENTITY_RE.sub(lambda m: _ENTITY_MAP.get(m.group(1), ""), text)
    text = _NUMERIC_ENTITY_RE.sub(lambda m: chr(int(m.group(1))), text)
    return text.strip(" .")


def _parse_rss_date(s: str) -> dt.datetime | None:
    """Parse RFC-2822 date string, ignoring non-standard AEST/AEDT timezone name."""
    if not s:
        return None
    try:
        t = parsedate(s)
        if t and t[0] > 2000:   # year 0001 = sentinel for "no end time"
            return dt.datetime(*t[:6])
    except Exception:
        pass
    return None


def _fmt_date(d: dt.datetime) -> str:
    """e.g. 'Saturday 5 July 2026'"""
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def _fmt_time(d: dt.datetime) -> str:
    """e.g. '8am', '10:30am', '2:30pm'"""
    hour, minute = d.hour, d.minute
    suffix = "am" if hour < 12 else "pm"
    h = hour % 12 or 12
    return f"{h}:{minute:02d}{suffix}" if minute else f"{h}{suffix}"


def _is_midnight(d: dt.datetime) -> bool:
    return d.hour == 0 and d.minute == 0 and d.second == 0


def _extract_cost(html: str) -> str:
    """Pull cost text from the <h3>Cost</h3> section of bodytext HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for heading in soup.find_all(["h3", "h4", "strong"]):
        if "cost" in heading.get_text().lower():
            nxt = heading.find_next_sibling()
            if nxt:
                raw = nxt.get_text(" ", strip=True)
                # Normalise: "Free." / "Free&nbsp;drop-in activity." → "Free"
                if re.search(r'\bfree\b', raw, re.IGNORECASE):
                    return "Free"
                # Take only the first sentence / up to 60 chars to keep cost concise
                first = re.split(r'[.!?]', raw)[0].strip()
                if first:
                    return first
    return ""


def _extract_booking_link(html: str) -> str:
    """Find the first registration/booking link in bodytext HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text().lower()
        if any(kw in href.lower() or kw in text for kw in
               ("eventbrite", "register", "book", "ticket", "waiting")):
            return href
    return ""


# ── RSS parsing ────────────────────────────────────────────────────────────────

def _parse_feed(xml_text: str) -> list[dict]:
    """Parse GRC What's On RSS feed into a list of structured event dicts."""
    events = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"[EVENTS] RSS XML parse error: {e}")
        return events

    channel = root.find("channel")
    if channel is None:
        return events

    for item in channel.findall("item"):
        title       = item.findtext("title", "").strip()
        description = _clean_entity_artifacts(item.findtext("description", "").strip())
        bodytext    = item.findtext("bodytext", "")
        # Location: first line only, clean HTML artifacts and trailing punctuation
        raw_loc     = item.findtext("location", "").split("\n")[0]
        location    = _clean_entity_artifacts(raw_loc).rstrip("/").strip()
        guid        = item.findtext("guid", "")

        start = _parse_rss_date(item.findtext("startDate", ""))
        end   = _parse_rss_date(item.findtext("endDate", ""))

        cost         = _extract_cost(bodytext)
        booking_link = _extract_booking_link(bodytext)

        # Build time range string — treat 00:00–00:00 as all-day
        if start:
            if _is_midnight(start) and (end is None or _is_midnight(end)):
                time_str = "All day"
            else:
                time_str = _fmt_time(start)
                if end and not _is_midnight(end):
                    time_str += f" to {_fmt_time(end)}"
        else:
            time_str = ""

        if not title:
            continue

        events.append({
            "title":        title,
            "description":  description[:250] if description else "",
            "start":        start.isoformat() if start else None,
            "date_str":     _fmt_date(start) if start else "Date TBC",
            "time_str":     time_str,
            "location":     location,
            "cost":         cost,
            "booking_link": booking_link,
            "url":          EVENTS_URL,  # no per-event URL in RSS; link to listing page
            "guid":         guid,
        })

    # Sort ascending by start date (soonest first)
    events.sort(key=lambda e: e["start"] or "9999")
    return events


# ── Public API ─────────────────────────────────────────────────────────────────

def get_events(force_refresh: bool = False) -> list[dict]:
    """Return the cached event list, refreshing if stale or forced."""
    global _CACHE, _CACHE_TS
    with _CACHE_LOCK:
        now = time.monotonic()
        if not force_refresh and _CACHE and (now - _CACHE_TS) < CACHE_TTL:
            return list(_CACHE)
        try:
            resp = requests.get(RSS_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            parsed = _parse_feed(resp.text)
            if parsed:
                _CACHE = parsed
                _CACHE_TS = now
                logger.info(f"[EVENTS] Refreshed cache: {len(parsed)} events")
            else:
                logger.warning("[EVENTS] RSS returned no events — keeping stale cache")
        except Exception as e:
            logger.warning(f"[EVENTS] RSS fetch failed: {e} — using stale cache")
        return list(_CACHE)


def format_events_for_system_prompt(events: list[dict]) -> str:
    """Compact event listing optimised for embedding in the LLM system prompt.

    Keeps each event to one line so it consumes minimal tokens while still
    giving the model everything it needs to answer questions like:
    "any free events?", "what's on this weekend?", "anything for kids?"
    """
    if not events:
        return "No upcoming events currently listed."

    today = dt.datetime.now()
    lines = [f"UPCOMING GRC EVENTS (as of {today.strftime('%d %B %Y')}, soonest first):"]

    for i, e in enumerate(events, 1):
        parts = [e["title"]]
        if e["date_str"] != "Date TBC":
            parts.append(e["date_str"])
        if e["time_str"] and e["time_str"] != "All day":
            parts.append(e["time_str"])
        elif e["time_str"] == "All day":
            parts.append("all day")
        if e["location"]:
            parts.append(e["location"])
        if e["cost"]:
            parts.append(e["cost"])
        if e["description"]:
            # Trim description to ~80 chars to keep lines short
            desc = e["description"][:80].rstrip()
            if len(e["description"]) > 80:
                desc += "..."
            parts.append(desc)
        lines.append(f"{i}. {' | '.join(parts)}")

    lines.append(f"Full listings & bookings: {EVENTS_URL}")
    return "\n".join(lines)


def format_events_for_tool(events: list[dict]) -> str:
    """Format event list into a compact, LLM-readable block for the tool result.

    Includes all fields so the LLM can answer questions like:
    "What's free?", "What's on this weekend?", "Any events for kids?"
    """
    if not events:
        return (
            "No upcoming events found. "
            "For the latest information visit georgesriver.nsw.gov.au/Whats-On"
        )

    today = dt.datetime.now()
    lines = [
        f"Georges River Council has {len(events)} upcoming event(s) "
        f"as of {today.strftime('%d %B %Y')}:\n"
    ]

    for i, e in enumerate(events, 1):
        parts = [f"{i}. {e['title']}"]
        if e["date_str"] != "Date TBC":
            parts.append(f"   Date: {e['date_str']}")
        if e["time_str"]:
            parts.append(f"   Time: {e['time_str']}")
        if e["location"]:
            parts.append(f"   Venue: {e['location']}")
        if e["cost"]:
            parts.append(f"   Cost: {e['cost']}")
        if e["description"]:
            parts.append(f"   About: {e['description']}")
        if e["booking_link"]:
            parts.append(f"   Bookings: {e['booking_link']}")
        lines.append("\n".join(parts))

    lines.append(f"\nFull listings: {EVENTS_URL}")
    return "\n\n".join(lines)


# ── Dev test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    events = get_events(force_refresh=True)
    print(format_events_for_tool(events))
