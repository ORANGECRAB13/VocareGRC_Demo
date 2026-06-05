#!/usr/bin/env python3
"""
test_bin_lookup.py — Test the bin collection lookup for a given address.

Usage:
    python test_bin_lookup.py "50 Vine Street Hurstville"
    python test_bin_lookup.py  # prompts for address
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "GRC_pilot"))

from tools import _correct_address
from grc_wastetrack import get_bin_collection_details, format_voice_response


def lookup(address: str):
    address = _correct_address(address.strip())
    print(f"\nAddress:  {address}")
    print("-" * 50)

    result = get_bin_collection_details(address)
    voice = format_voice_response(result)
    if voice:
        print(f"[Wastetrack] {voice}")
    else:
        print(f"[Wastetrack] failed: {result.get('error')}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if args:
        lookup(" ".join(args))
    else:
        address = input("Enter address: ")
        lookup(address)
