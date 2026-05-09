"""
test_tools.py — Interactive CLI for testing all GRC bot tools.

Run:
    python test_tools.py

Tests the exact same tool functions wired into bot.py so results
reflect what the voice agent would say.
"""

import sys
import os
from pathlib import Path

# Mirror bot.py's sys.path so imports resolve identically
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from database import init_db
from tools import (
    get_bin_collection_details,
    check_service_status,
    book_bulky_waste,
    update_booking_date,
    get_council_events,
)

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────

def prompt(label: str, required: bool = True) -> str:
    while True:
        val = input(f"  {label}: ").strip()
        if val or not required:
            return val
        print("  (required — please enter a value)")


def run_tool(name: str, result: str):
    print()
    print(f"  BOT SAYS: {result}")
    print()


# ── Tool handlers ─────────────────────────────────────────────────────────────

def test_bin_collection():
    print("\n[ Bin Collection Day ]")
    address = prompt("Street address (e.g. 6 Gannons Avenue Hurstville)")
    result = get_bin_collection_details({"address": address})
    run_tool("get_bin_collection_details", result)


def test_service_status():
    print("\n[ Check Bulky Waste Entitlements ]")
    phone = prompt("Phone number")
    result = check_service_status({"phone": phone})
    run_tool("check_service_status", result)


def test_book_bulky_waste():
    print("\n[ Book Bulky Waste Collection ]")
    name     = prompt("Full name")
    phone    = prompt("Phone number")
    address  = prompt("Property address")
    date     = prompt("Preferred collection date (e.g. 2026-06-15)")
    result   = book_bulky_waste({
        "name": name, "phone": phone,
        "address": address, "preferred_date": date,
    })
    run_tool("book_bulky_waste", result)


def test_council_events():
    print("\n[ Council Events ]")
    query = prompt("Keyword filter (e.g. free, kids, sport) — or press Enter for all", required=False)
    result = get_council_events({"query": query})
    run_tool("get_council_events", result)


def test_update_booking():
    print("\n[ Change Booking Date ]")
    phone    = prompt("Phone number")
    name     = prompt("Full name")
    new_date = prompt("New preferred date (e.g. 2026-07-01)")
    result   = update_booking_date({"phone": phone, "name": name, "new_date": new_date})
    run_tool("update_booking_date", result)


# ── Menu ──────────────────────────────────────────────────────────────────────

MENU = [
    ("Bin collection day lookup",         test_bin_collection),
    ("Check bulky waste entitlements",    test_service_status),
    ("Book bulky waste collection",       test_book_bulky_waste),
    ("Change bulky waste booking date",   test_update_booking),
    ("Council events (What's On)",        test_council_events),
]


def main():
    print("=" * 52)
    print("  GRC Bot Tool Tester")
    print("  (Ctrl-C or 'q' to quit)")
    print("=" * 52)

    while True:
        print()
        for i, (label, _) in enumerate(MENU, 1):
            print(f"  {i}. {label}")
        print()

        choice = input("Select tool (1-5): ").strip().lower()
        if choice in ("q", "quit", "exit"):
            break
        if not choice.isdigit() or not (1 <= int(choice) <= len(MENU)):
            print("  Invalid choice.")
            continue

        _, handler = MENU[int(choice) - 1]
        try:
            handler()
        except KeyboardInterrupt:
            print()
            break
        except Exception as e:
            print(f"\n  ERROR: {e}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
