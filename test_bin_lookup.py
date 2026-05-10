#!/usr/bin/env python3
"""
test_bin_lookup.py — Test the bin collection lookup for a given address.

Usage:
    python test_bin_lookup.py "50 Vine Street Hurstville"          # Wastetrack (default)
    python test_bin_lookup.py --polygon "50 Vine Street Hurstville"  # polygon only
    python test_bin_lookup.py --both "50 Vine Street Hurstville"     # both methods
    python test_bin_lookup.py  # prompts for address
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "GRC_pilot"))

from tools import get_bin_collection_zone, _correct_address
from grc_wastetrack import get_bin_collection_details, format_voice_response


def lookup(address: str, mode: str = "wastetrack"):
    address = _correct_address(address.strip())
    print(f"\nAddress:  {address}")
    print("-" * 50)

    if mode == "wastetrack":
        result = get_bin_collection_details(address)
        voice = format_voice_response(result)
        if voice:
            print(f"[Wastetrack] {voice}")
        else:
            print(f"[Wastetrack] failed: {result.get('error')} — trying polygon fallback...")
            fallback = get_bin_collection_zone({"address": address})
            print(f"[Polygon]    {fallback}")

    elif mode == "polygon":
        fallback = get_bin_collection_zone({"address": address})
        print(f"[Polygon]    {fallback}")

    elif mode == "both":
        result = get_bin_collection_details(address)
        voice = format_voice_response(result)
        if voice:
            print(f"[Wastetrack] {voice}")
        else:
            print(f"[Wastetrack] failed: {result.get('error')}")
        fallback = get_bin_collection_zone({"address": address})
        print(f"[Polygon]    {fallback}")


if __name__ == "__main__":
    args = sys.argv[1:]

    mode = "wastetrack"
    if "--polygon" in args:
        mode = "polygon"
        args.remove("--polygon")
    elif "--both" in args:
        mode = "both"
        args.remove("--both")

    if args:
        lookup(" ".join(args), mode)
    else:
        address = input("Enter address: ")
        lookup(address, mode)
