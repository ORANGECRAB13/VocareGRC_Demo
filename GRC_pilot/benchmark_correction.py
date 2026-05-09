"""
benchmark_correction.py — Street correction benchmark for GRC bin zone lookup.

Runs realistic STT transcription variants through the geocoding pipeline
both WITHOUT and WITH street name correction, then prints:

  1. Side-by-side results table
  2. Latency breakdown (module load / cold correction / warm cache / geocoding)
  3. Overall accuracy summary by error category

Usage:
    python benchmark_correction.py            # correction layer only (no API calls)
    python benchmark_correction.py --geocode  # also call Google Maps (needs API key)
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from typing import NamedTuple

import requests

# ---------------------------------------------------------------------------
# Test cases  (stt_input, canonical_truth, error_type)
# canonical_truth = expected corrector output; None = expect no match returned
# ---------------------------------------------------------------------------

CASES: list[tuple[str, str | None, str]] = [
    # Exact / clean
    ("23 Gannons Avenue",       "Gannons Avenue",       "exact"),
    ("Forest Road",             "Forest Road",          "exact"),
    ("Penshurst Street",        "Penshurst Street",     "exact"),
    ("Woniora Road",            "Woniora Road",         "exact"),
    ("Stoney Creek Road",       "Stoney Creek Road",    "exact"),
    ("Lugarno Parade",          "Lugarno Parade",       "exact"),
    ("Narwee Avenue",           "Narwee Avenue",        "exact"),
    ("Kyle Parade",             "Kyle Parade",          "exact"),
    ("Connells Point Road",     "Connells Point Road",  "exact"),
    ("Kingsgrove Road",         "Kingsgrove Road",      "exact"),
    # Abbreviations
    ("Gannons Av",              "Gannons Avenue",       "abbrev"),
    ("Forest Rd",               "Forest Road",          "abbrev"),
    ("Penshurst St",            "Penshurst Street",     "abbrev"),
    ("Lugarno Pde",             "Lugarno Parade",       "abbrev"),
    ("Narwee Av",               "Narwee Avenue",        "abbrev"),
    ("Kyle Pde",                "Kyle Parade",          "abbrev"),
    ("Woniora Rd",              "Woniora Road",         "abbrev"),
    ("Connells Point Rd",       "Connells Point Road",  "abbrev"),
    ("Stoney Creek Rd",         "Stoney Creek Road",    "abbrev"),
    ("Hurstville Rd",           "Hurstville Road",      "abbrev"),
    # Phonetic first-char swap (c/g/k/q)
    ("Canons Street",           "Gannons Avenue",       "phonetic"),
    ("Cannons Avenue",          "Gannons Avenue",       "phonetic"),
    ("Ganons Avenue",           "Gannons Avenue",       "phonetic"),
    ("Kogarar Road",            "Kogarah High School",  "phonetic"),
    # Double / dropped consonant
    ("Forrest Road",            "Forest Road",          "consonant"),
    ("Penshhurst Street",       "Penshurst Street",     "consonant"),
    ("Narwy Avenue",            "Narwee Avenue",        "consonant"),
    # Vowel errors
    ("Wanniora Road",           "Woniora Road",         "vowel"),
    ("Stony Creek Road",        "Stoney Creek Road",    "vowel"),
    ("Kingsgruve Road",         "Kingsgrove Road",      "vowel"),
    # Punctuation / whitespace artefacts
    ("Connell's Point Road",    "Connells Point Road",  "punctuation"),
    ("Stoney Creek  Road",      "Stoney Creek Road",    "punctuation"),
    # Number-prefixed full address
    ("14 Forest Road",          "Forest Road",          "number prefix"),
    ("7 Penshurst Street",      "Penshurst Street",     "number prefix"),
    ("52 Woniora Road",         "Woniora Road",         "number prefix"),
    ("3 Narwee Avenue",         "Narwee Avenue",        "number prefix"),
    ("18 Stoney Creek Road",    "Stoney Creek Road",    "number prefix"),
    # No-match / edge cases
    ("Xylophone Boulevard",     None,                   "no match"),
    ("",                        None,                   "no match"),
    ("123",                     None,                   "no match"),
]

# Inputs used for the pure latency benchmark (unique, avoids cache hits)
_LAT_INPUTS = [
    "Canons Street", "Forrest Road", "Penshhurst St", "Narwy Av",
    "Stony Creek Rd", "Wanniora Rd", "Lugarno Pde", "Gloucester Rd",
    "Belmore Road", "Penshurst St", "Kyle Pde", "Woniora Rd",
    "Stoney Creek Road", "Connells Point Rd", "Beverly Hills",
    "Kingsgrove Rd", "Princes Hwy", "Rocky Point Rd", "Forest Road",
    "Gannons Avenue",
]

# ---------------------------------------------------------------------------
# Geocoding helpers
# ---------------------------------------------------------------------------

_GEO_CACHE: dict[str, str | None] = {}


def _geocode_zone(address: str, api_key: str) -> tuple[str | None, float]:
    """Return (collection_day_or_error, elapsed_ms)."""
    key = address.lower().strip()
    if key in _GEO_CACHE:
        return _GEO_CACHE[key], 0.0

    t0 = time.perf_counter()
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address + ", Georges River NSW", "key": api_key},
            timeout=10,
        )
        data = resp.json()
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        _GEO_CACHE[key] = f"ERR:{exc}"
        return _GEO_CACHE[key], elapsed

    elapsed = (time.perf_counter() - t0) * 1000

    results = data.get("results", [])
    if not results:
        _GEO_CACHE[key] = None
        return None, elapsed

    loc = results[0]["geometry"]["location"]
    import bin_zones
    zone = bin_zones.find_zone_for_point(loc["lat"], loc["lng"])
    day = zone["collection_day"] if zone else "OUT_OF_ZONE"
    _GEO_CACHE[key] = day
    return day, elapsed


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class Result(NamedTuple):
    stt_input:       str
    canonical:       str | None
    error_type:      str
    corrected_to:    str | None
    conf:            float
    correction_hit:  bool
    correction_ms:   float
    zone_raw:        str | None
    zone_fixed:      str | None
    geo_raw_ms:      float
    geo_fixed_ms:    float


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(geocode: bool, api_key: str) -> list[Result]:
    import street_corrector as _sc
    corrector = _sc.get_corrector()

    results: list[Result] = []
    for stt_input, canonical, error_type in CASES:
        t0 = time.perf_counter()
        cr = corrector.correct_street(stt_input)
        correction_ms = (time.perf_counter() - t0) * 1000

        corrected_to = cr[0] if cr else None
        conf         = cr[1] if cr else 0.0

        if canonical is None:
            correction_hit = corrected_to is None
        else:
            correction_hit = corrected_to == canonical

        zone_raw = zone_fixed = None
        geo_raw_ms = geo_fixed_ms = 0.0

        if geocode and api_key:
            raw_addr = stt_input or "(empty)"
            zone_raw, geo_raw_ms = _geocode_zone(raw_addr, api_key)
            fixed_addr = corrected_to or stt_input
            if fixed_addr != raw_addr:
                zone_fixed, geo_fixed_ms = _geocode_zone(fixed_addr, api_key)
            else:
                zone_fixed = zone_raw

        results.append(Result(
            stt_input      = stt_input,
            canonical      = canonical,
            error_type     = error_type,
            corrected_to   = corrected_to,
            conf           = conf,
            correction_hit = correction_hit,
            correction_ms  = correction_ms,
            zone_raw       = zone_raw,
            zone_fixed     = zone_fixed,
            geo_raw_ms     = geo_raw_ms,
            geo_fixed_ms   = geo_fixed_ms,
        ))

    return results


# ---------------------------------------------------------------------------
# Latency benchmark
# ---------------------------------------------------------------------------

def latency_breakdown(api_key: str, geocode: bool) -> None:
    import street_corrector as _sc

    SEP = "=" * 60

    print()
    print(SEP)
    print("  LATENCY BREAKDOWN")
    print(SEP)

    # 1. Module / singleton load
    t0 = time.perf_counter()
    corrector = _sc.StreetCorrector(_sc._default_path())
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"  Module load (parse + index build) : {load_ms:.1f} ms")
    print(f"  Streets indexed                   : {corrector.street_count}")

    # 2. Cold cache — unique inputs, no prior cache
    corrector._cache.clear()
    cold_times: list[float] = []
    for inp in _LAT_INPUTS:
        corrector._cache.clear()
        t0 = time.perf_counter()
        corrector.correct_street(inp)
        cold_times.append((time.perf_counter() - t0) * 1000)

    print()
    print(f"  Cold-cache correction ({len(cold_times)} unique inputs):")
    print(f"    min   {min(cold_times):.3f} ms")
    print(f"    mean  {statistics.mean(cold_times):.3f} ms")
    print(f"    p95   {sorted(cold_times)[int(len(cold_times)*0.95)]:.3f} ms")
    print(f"    max   {max(cold_times):.3f} ms")

    # 3. Warm cache — same inputs, all already cached
    # Prime cache first
    for inp in _LAT_INPUTS:
        corrector.correct_street(inp)
    warm_times: list[float] = []
    for _ in range(500):
        for inp in _LAT_INPUTS:
            t0 = time.perf_counter()
            corrector.correct_street(inp)
            warm_times.append((time.perf_counter() - t0) * 1000)

    print()
    print(f"  Warm-cache correction ({len(warm_times)} calls):")
    print(f"    min   {min(warm_times):.4f} ms")
    print(f"    mean  {statistics.mean(warm_times):.4f} ms")
    print(f"    p95   {sorted(warm_times)[int(len(warm_times)*0.95)]:.4f} ms")
    print(f"    max   {max(warm_times):.4f} ms")

    # 4. Geocoding round-trip (single live call, if enabled)
    if geocode and api_key:
        test_addr = "Forest Road, Georges River NSW"
        # Ensure not cached
        _GEO_CACHE.pop(test_addr.lower().strip(), None)
        _, geo_ms = _geocode_zone("Forest Road", api_key)
        print()
        print(f"  Google Maps geocoding (single call) : {geo_ms:.0f} ms  (network latency)")
        print(f"  [Subsequent calls hit local cache   : ~0 ms]")
    else:
        print()
        print("  Google Maps geocoding               : -- (pass --geocode to measure)")

    # 5. End-to-end estimate
    mean_cold = statistics.mean(cold_times)
    mean_warm = statistics.mean(warm_times)
    print()
    print("  End-to-end estimate (correction only, no geocoding):")
    print(f"    First call per address  : ~{mean_cold:.2f} ms  (cold)")
    print(f"    Repeat same address     : ~{mean_warm:.4f} ms  (cache hit)")
    print()
    print("  1 ms budget consumed by correction layer:")
    pct = mean_cold / 1.0 * 100
    bar = "#" * min(int(pct / 5), 20)
    print(f"    [{bar:<20}]  {pct:.1f}% of 1 ms budget (cold)")
    print(SEP)


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------

def _trunc(s: str | None, n: int) -> str:
    if s is None:
        return "-"
    return s if len(s) <= n else s[: n - 1] + "~"


def print_table(results: list[Result], geocode: bool) -> None:
    W_IN   = 32
    W_CAN  = 24
    W_CORR = 24
    W_TYPE = 18
    extra  = 26 if geocode else 0
    sep    = "-" * (W_IN + W_CAN + W_CORR + W_TYPE + 22 + extra)

    if geocode:
        header = (
            f"{'STT INPUT':<{W_IN}}  {'CANONICAL':<{W_CAN}}  "
            f"{'CORRECTED TO':<{W_CORR}}  {'CONF':>5}  {'MS':>5}  {'HIT':>3}  "
            f"{'ZONE_RAW':<10}  {'ZONE_FIX':<10}  {'TYPE':<{W_TYPE}}"
        )
    else:
        header = (
            f"{'STT INPUT':<{W_IN}}  {'CANONICAL':<{W_CAN}}  "
            f"{'CORRECTED TO':<{W_CORR}}  {'CONF':>5}  {'MS':>5}  {'HIT':>3}  "
            f"{'TYPE':<{W_TYPE}}"
        )

    print()
    print(sep)
    print(header)
    print(sep)

    prev_type = None
    for r in results:
        if r.error_type != prev_type:
            if prev_type is not None:
                print()
            prev_type = r.error_type

        hit_str = "YES" if r.correction_hit else "NO "

        if geocode:
            row = (
                f"{_trunc(r.stt_input, W_IN):<{W_IN}}  "
                f"{_trunc(r.canonical, W_CAN):<{W_CAN}}  "
                f"{_trunc(r.corrected_to, W_CORR):<{W_CORR}}  "
                f"{r.conf:>5.3f}  {r.correction_ms:>5.2f}  {hit_str}  "
                f"{_trunc(r.zone_raw, 10):<10}  "
                f"{_trunc(r.zone_fixed, 10):<10}  "
                f"{r.error_type:<{W_TYPE}}"
            )
        else:
            row = (
                f"{_trunc(r.stt_input, W_IN):<{W_IN}}  "
                f"{_trunc(r.canonical, W_CAN):<{W_CAN}}  "
                f"{_trunc(r.corrected_to, W_CORR):<{W_CORR}}  "
                f"{r.conf:>5.3f}  {r.correction_ms:>5.2f}  {hit_str}  "
                f"{r.error_type:<{W_TYPE}}"
            )
        print(row)

    print(sep)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(results: list[Result], geocode: bool) -> None:
    total     = len(results)
    hits      = sum(1 for r in results if r.correction_hit)
    misses    = total - hits
    conf_vals = [r.conf for r in results if r.conf > 0]
    avg_conf  = statistics.mean(conf_vals) if conf_vals else 0.0

    by_type: dict[str, tuple[int, int]] = {}
    for r in results:
        h, t = by_type.get(r.error_type, (0, 0))
        by_type[r.error_type] = (h + int(r.correction_hit), t + 1)

    SEP = "=" * 60
    print()
    print(SEP)
    print("  ACCURACY SUMMARY")
    print(SEP)
    print(f"  Total cases         : {total}")
    print(f"  Correct             : {hits}  ({hits/total*100:.0f}%)")
    print(f"  Wrong / missed      : {misses}  ({misses/total*100:.0f}%)")
    print(f"  Avg confidence      : {avg_conf:.3f}")
    print(f"  High conf (>= 0.85) : {sum(1 for r in results if r.conf >= 0.85)}")
    print()
    print("  BY CATEGORY:")
    for etype, (h, t) in sorted(by_type.items()):
        bar = "#" * int(h / t * 20) + "." * (20 - int(h / t * 20))
        print(f"    {etype:<22}  [{bar}]  {h}/{t}")

    if geocode:
        geo_improved = sum(
            1 for r in results
            if r.zone_raw is None
            and r.zone_fixed not in (None, "OUT_OF_ZONE")
        )
        print()
        print(f"  Zones rescued by correction : {geo_improved}")

    print(SEP)
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Street correction benchmark")
    parser.add_argument(
        "--geocode", action="store_true",
        help="Call Google Maps API (requires GOOGLE_MAPS_API_KEY env var)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    geocode = args.geocode and bool(api_key)

    print()
    print("GRC Street Name Correction  —  Benchmark")
    print("=" * 60)

    import street_corrector as _sc
    corrector = _sc.get_corrector()
    print(f"Streets loaded : {corrector.street_count}")
    if args.geocode and not api_key:
        print("Geocoding      : DISABLED (GOOGLE_MAPS_API_KEY not set)")
    elif geocode:
        print("Geocoding      : ENABLED")
    else:
        print("Geocoding      : DISABLED  (pass --geocode to enable)")

    # Run results table
    results = run(geocode=geocode, api_key=api_key)
    print_table(results, geocode=geocode)
    print_summary(results, geocode=geocode)

    # Latency breakdown (always shown)
    latency_breakdown(api_key=api_key, geocode=geocode)
