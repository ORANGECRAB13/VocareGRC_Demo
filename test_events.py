#!/usr/bin/env python3
"""
test_events.py — Inspect the live GRC What's On events the agent references.

Usage:
    python test_events.py                  # show all events (system prompt view)
    python test_events.py --raw            # show full raw event details
    python test_events.py --filter free    # filter events by keyword
    python test_events.py --raw --filter kids
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "GRC_pilot"))

from grc_events import get_events, format_events_for_system_prompt, EVENTS_URL
import datetime as dt


def show_system_prompt_view(events: list[dict]):
    """Print exactly what the agent sees in its system prompt."""
    print("\n" + "=" * 70)
    print("AGENT SYSTEM PROMPT VIEW (what the LLM is given)")
    print("=" * 70)
    print(format_events_for_system_prompt(events))


def show_raw(events: list[dict]):
    """Print full structured detail for every event."""
    print("\n" + "=" * 70)
    print(f"RAW EVENT DATA — {len(events)} event(s)")
    print("=" * 70)
    for i, e in enumerate(events, 1):
        print(f"\n[{i}] {e['title']}")
        print(f"    Date:     {e['date_str']}")
        print(f"    Time:     {e['time_str'] or '—'}")
        print(f"    Location: {e['location'] or '—'}")
        print(f"    Cost:     {e['cost'] or '—'}")
        if e["description"]:
            print(f"    About:    {e['description']}")
        if e["booking_link"]:
            print(f"    Booking:  {e['booking_link']}")
        print(f"    Start ISO: {e['start'] or '—'}")


def filter_events(events: list[dict], keyword: str) -> list[dict]:
    kw = keyword.lower()
    return [
        e for e in events
        if kw in e["title"].lower()
        or kw in e["description"].lower()
        or kw in e["location"].lower()
        or kw in e["cost"].lower()
    ]


if __name__ == "__main__":
    args = sys.argv[1:]

    raw = "--raw" in args
    if raw:
        args.remove("--raw")

    keyword = None
    if "--filter" in args:
        idx = args.index("--filter")
        if idx + 1 < len(args):
            keyword = args[idx + 1]
            args.remove("--filter")
            args.remove(keyword)

    print(f"Fetching live events from: {EVENTS_URL}")
    events = get_events(force_refresh=True)
    print(f"Found {len(events)} event(s)  |  as of {dt.datetime.now().strftime('%d %B %Y %H:%M')}")

    if keyword:
        events = filter_events(events, keyword)
        print(f"Filtered to {len(events)} event(s) matching '{keyword}'")

    if raw:
        show_raw(events)
    else:
        show_system_prompt_view(events)
