"""
grc_events.py — Georges River Council What's On events.

Primary source: HTML scraping of the What's On listing page.
Fallback:       RSS feed (used if HTML scrape fails or returns nothing).

The listing page contains richer, up-to-date data than the RSS feed.
Each event card provides: date, title, short description, and a direct URL.
"""

import re
import time
import threading
import datetime as dt
from email.utils import parsedate
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from loguru import logger

# ── Constants ──────────────────────────────────────────────────────────────────

BASE_URL   = "https://www.georgesriver.nsw.gov.au"
EVENTS_URL = f"{BASE_URL}/whats-on"
RSS_URL    = f"{BASE_URL}/Whats-On?rss=Whats-on"
CACHE_TTL  = 30 * 60  # 30 minutes

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# ── Cache ──────────────────────────────────────────────────────────────────────

_CACHE: list[dict] = []
_CACHE_TS: float = 0.0
_CACHE_LOCK = threading.Lock()

# ── HTML scraper (primary) ─────────────────────────────────────────────────────

def _parse_html_date(day: str, month_year: str) -> str:
    """Combine '11' + 'May 2026' → '11 May 2026'."""
    return f"{day.strip()} {month_year.strip()}"


def _parse_html_date_to_iso(day: str, month_year: str) -> str | None:
    """Return ISO date string e.g. '2026-05-11', or None on failure."""
    try:
        d = dt.datetime.strptime(f"{day.strip()} {month_year.strip()}", "%d %B %Y")
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _scrape_html() -> list[dict]:
    """Scrape the GRC What's On listing page and return structured event dicts."""
    try:
        resp = requests.get(EVENTS_URL, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[EVENTS] HTML fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.find_all("li", class_="event-summary")

    if not items:
        logger.warning("[EVENTS] HTML scrape found no event-summary items")
        return []

    events = []
    seen: set[str] = set()  # deduplicate by (date, title)

    for item in items:
        try:
            left = item.find("div", class_="left")
            day = left.find("span").get_text(strip=True) if left else ""

            month_el = item.find("div", class_="month")
            month_year = month_el.get_text(strip=True) if month_el else ""

            title_el = item.find("div", class_="title")
            title = title_el.get_text(strip=True) if title_el else ""

            details_el = item.find("div", class_="details")
            description = details_el.get_text(" ", strip=True) if details_el else ""
            # Clean non-breaking spaces and extra whitespace
            description = re.sub(r"[\xa0\u200b]+", " ", description).strip()

            a_tag = item.find("a", href=True)
            href = a_tag["href"] if a_tag else ""
            # Make absolute URL
            if href and not href.startswith("http"):
                href = BASE_URL + href

            header_el = item.find("div", class_="header")
            header_classes = header_el.get("class", []) if header_el else []
            organiser = "community" if "community-event" in header_classes else "council"

            if not title or not day or not month_year:
                continue

            date_str = _parse_html_date(day, month_year)
            iso = _parse_html_date_to_iso(day, month_year)

            key = (date_str, title)
            if key in seen:
                continue
            seen.add(key)

            events.append({
                "title":        title,
                "description":  description[:300] if description else "",
                "date_str":     date_str,
                "start":        iso,
                "time_str":     "",
                "location":     "",
                "cost":         "",
                "booking_link": href,
                "url":          href or EVENTS_URL,
                "organiser":    organiser,
            })
        except Exception as e:
            logger.debug(f"[EVENTS] Skipping malformed item: {e}")
            continue

    # Sort ascending by ISO date
    events.sort(key=lambda e: e["start"] or "9999")
    logger.info(f"[EVENTS] HTML scrape: {len(events)} unique events")
    return events


# ── RSS fallback (secondary) ───────────────────────────────────────────────────

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
    text = _ENTITY_RE.sub(lambda m: _ENTITY_MAP.get(m.group(1), ""), text)
    text = _NUMERIC_ENTITY_RE.sub(lambda m: chr(int(m.group(1))), text)
    return text.strip(" .")


def _parse_rss_date(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = parsedate(s)
        if t and t[0] > 2000:
            return dt.datetime(*t[:6])
    except Exception:
        pass
    return None


def _fmt_date(d: dt.datetime) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def _scrape_rss() -> list[dict]:
    """Parse the GRC RSS feed as a fallback source."""
    try:
        resp = requests.get(
            RSS_URL,
            headers={"User-Agent": _HEADERS["User-Agent"], "Accept": "application/rss+xml,*/*"},
            timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as e:
        logger.warning(f"[EVENTS] RSS fallback failed: {e}")
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    events = []
    seen: set[str] = set()

    for item in channel.findall("item"):
        title = item.findtext("title", "").strip()
        description = _clean_entity_artifacts(item.findtext("description", "").strip())
        start = _parse_rss_date(item.findtext("startDate", ""))
        raw_loc = item.findtext("location", "").split("\n")[0]
        location = _clean_entity_artifacts(raw_loc).rstrip("/").strip()
        guid = item.findtext("guid", "")

        if not title:
            continue

        date_str = _fmt_date(start) if start else "Date TBC"
        iso = start.strftime("%Y-%m-%d") if start else None

        key = (date_str, title)
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "title":        title,
            "description":  description[:300] if description else "",
            "date_str":     date_str,
            "start":        iso,
            "time_str":     "",
            "location":     location,
            "cost":         "",
            "booking_link": guid,
            "url":          EVENTS_URL,
            "organiser":    "council",
        })

    events.sort(key=lambda e: e["start"] or "9999")
    logger.info(f"[EVENTS] RSS fallback: {len(events)} unique events")
    return events


# ── Public API ─────────────────────────────────────────────────────────────────

def get_events(force_refresh: bool = False) -> list[dict]:
    """Return the cached event list, refreshing from HTML (or RSS) if stale."""
    global _CACHE, _CACHE_TS
    with _CACHE_LOCK:
        now = time.monotonic()
        if not force_refresh and _CACHE and (now - _CACHE_TS) < CACHE_TTL:
            return list(_CACHE)

        events = _scrape_html()
        if not events:
            logger.warning("[EVENTS] HTML scrape empty — falling back to RSS")
            events = _scrape_rss()

        if events:
            _CACHE = events
            _CACHE_TS = now
        else:
            logger.warning("[EVENTS] Both sources returned nothing — keeping stale cache")

        return list(_CACHE)


def filter_next_days(events: list[dict], days: int = 30) -> list[dict]:
    """Return only events starting within the next `days` days."""
    cutoff = (dt.datetime.now() + dt.timedelta(days=days)).strftime("%Y-%m-%d")
    today = dt.datetime.now().strftime("%Y-%m-%d")
    return [
        e for e in events
        if e["start"] and today <= e["start"] <= cutoff
    ]


def format_events_for_system_prompt(events: list[dict]) -> str:
    """Compact 30-day event listing for embedding in the LLM system prompt.

    Only includes events in the next 30 days to keep token count low.
    Events beyond 30 days are available via the get_future_events tool.
    """
    near = filter_next_days(events, days=30)
    if not near:
        return "No events in the next 30 days. Use get_future_events tool for later dates."

    today = dt.datetime.now()
    lines = [
        f"GRC EVENTS — NEXT 30 DAYS (as of {today.strftime('%d %B %Y')}, {len(near)} events):",
        "Note: for events beyond 30 days, call the get_future_events tool.",
    ]

    for i, e in enumerate(near, 1):
        parts = [e["title"]]
        if e["date_str"] != "Date TBC":
            parts.append(e["date_str"])
        if e["time_str"]:
            parts.append(e["time_str"])
        if e["location"]:
            parts.append(e["location"])
        if e["cost"]:
            parts.append(e["cost"])
        if e["description"]:
            desc = e["description"][:100].rstrip()
            if len(e["description"]) > 100:
                desc += "..."
            parts.append(desc)
        lines.append(f"{i}. {' | '.join(parts)}")

    lines.append(f"Full listings & bookings: {EVENTS_URL}")
    return "\n".join(lines)


def get_future_events(after_days: int = 30) -> str:
    """Return events starting after `after_days` days, formatted for LLM tool result.

    Called by the voice agent when the user asks about events beyond the 30-day
    CAG window (e.g. 'anything on in July?', 'what about next month?').
    """
    events = get_events()
    cutoff = (dt.datetime.now() + dt.timedelta(days=after_days)).strftime("%Y-%m-%d")
    future = [e for e in events if e["start"] and e["start"] > cutoff]

    if not future:
        return f"No events found after {after_days} days from today."

    today = dt.datetime.now()
    lines = [f"GRC events beyond the next {after_days} days ({len(future)} events):"]
    for i, e in enumerate(future, 1):
        parts = [e["title"], e["date_str"]]
        if e["description"]:
            parts.append(e["description"][:80] + ("..." if len(e["description"]) > 80 else ""))
        lines.append(f"{i}. {' | '.join(parts)}")
    lines.append(f"Full listings: {EVENTS_URL}")
    return "\n".join(lines)


def format_events_for_tool(events: list[dict]) -> str:
    """Detailed event listing for tool results."""
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
            parts.append(f"   Link: {e['booking_link']}")
        lines.append("\n".join(parts))

    lines.append(f"\nFull listings: {EVENTS_URL}")
    return "\n\n".join(lines)


# ── Dev test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    events = get_events(force_refresh=True)
    print(format_events_for_tool(events))
