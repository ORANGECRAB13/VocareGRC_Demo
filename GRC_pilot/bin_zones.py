"""
bin_zones.py — Pure-Python bin collection zone lookup.

Schedule model
--------------
General waste (red bin)   — every collection day (weekly).
Recycling (yellow bin)    — fortnightly, alternating with garden waste.
Garden waste (green bin)  — fortnightly, alternating with recycling.

The schedule is driven by schedules/all_zones.json:
  {
    "start_date": "2026-01-05",
    "pattern": ["recycling", "garden"],
    "offset": 0          ← optional, default 0
  }

For any collection date D:
  weeks = (D - start_date).days // 7
  type  = pattern[(weeks + offset) % 2]

Public API
----------
  find_zone_for_point(lat, lng) -> dict | None
  format_voice_prompt(zone)     -> str
"""

import json
import os
import re
from datetime import date, datetime, timedelta

_DAY_MAP = {
    "mon":   "Monday",
    "tues":  "Tuesday",
    "wed":   "Wednesday",
    "thurs": "Thursday",
    "fri":   "Friday",
}

# Derive zone_id key from display name: "Monday Zone 1" → "mon_zone1"
_DAY_NAME_TO_ABBR = {
    "Monday":    "mon",
    "Tuesday":   "tues",
    "Wednesday": "wed",
    "Thursday":  "thurs",
    "Friday":    "fri",
}

_DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# List of {zone_id, day, coordinates}
ZONES: list[dict] = []

# Pattern schedule keyed by zone_id
# {start_date: date, pattern: [str, str], offset: int}
_SCHEDULES: dict[str, dict] = {}


# ── Loaders ────────────────────────────────────────────────────────────────────

def _zone_id_from_name(name: str) -> str | None:
    """'Monday Zone 1' → 'mon_zone1'"""
    m = re.match(r"^(\w+)\s+Zone\s+(\d+)$", name, re.IGNORECASE)
    if not m:
        return None
    abbr = _DAY_NAME_TO_ABBR.get(m.group(1).capitalize())
    if not abbr:
        return None
    return f"{abbr}_zone{m.group(2)}"


def _load_zones() -> None:
    zones_dir = os.path.join(os.path.dirname(__file__), "api", "zones")
    if not os.path.isdir(zones_dir):
        return

    for fname in os.listdir(zones_dir):
        if not fname.endswith(".geojson"):
            continue
        m = re.match(r"^(\w+?)_zone(\d+)\.geojson$", fname, re.IGNORECASE)
        if not m:
            continue

        abbr = m.group(1).lower()
        zone_num = m.group(2)
        day = _DAY_MAP.get(abbr)
        if not day:
            continue

        zone_id = f"{abbr}_zone{zone_num}"
        fpath = os.path.join(zones_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                geojson = json.load(f)
        except Exception:
            continue

        for feature in geojson.get("features", []):
            geom = feature.get("geometry", {})
            if geom.get("type") == "Polygon":
                ZONES.append({
                    "zone_id": zone_id,
                    "day": day,
                    "coordinates": geom["coordinates"][0],
                })


def _load_schedules() -> None:
    fpath = os.path.join(os.path.dirname(__file__), "schedules", "all_zones.json")
    if not os.path.isfile(fpath):
        return
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        for zone in data.get("zones", []):
            zone_id = _zone_id_from_name(zone["name"])
            if not zone_id:
                continue
            rg = zone["recycling_garden"]
            _SCHEDULES[zone_id] = {
                "start_date": datetime.strptime(rg["start_date"], "%Y-%m-%d").date(),
                "pattern":    rg["pattern"],          # ["recycling","garden"] or reversed
                "offset":     rg.get("offset", 0),    # 0 or 1
            }
    except Exception:
        pass


# ── Geometry ───────────────────────────────────────────────────────────────────

def _ray_cast(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Return True if (lat, lng) is inside polygon using ray-casting."""
    x, y = lng, lat
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ── Schedule helpers ───────────────────────────────────────────────────────────

def bins_on_date(sched: dict, d: date) -> list[str]:
    """Return bins collected on date d for a zone: always 'general', plus recycling/garden."""
    start  = sched["start_date"]
    delta  = (d - start).days
    if delta < 0 or delta % 7 != 0:
        return ["general"]
    weeks   = delta // 7
    pattern = sched["pattern"]
    offset  = sched["offset"]
    bin_type = pattern[(weeks + offset) % len(pattern)]
    return ["general", bin_type]


def _next_of_type(sched: dict, today: date, target: str) -> date | None:
    """Return the next collection date >= today whose type matches target."""
    start   = sched["start_date"]
    pattern = sched["pattern"]
    offset  = sched["offset"]

    # Find first collection week on or after today
    if today <= start:
        d = start
    else:
        weeks_elapsed = (today - start).days // 7
        d = start + timedelta(weeks=weeks_elapsed)
        if d < today:
            d += timedelta(weeks=1)

    # d is a valid collection week; check up to 2 weeks (pattern length = 2)
    for _ in range(len(pattern)):
        weeks = (d - start).days // 7
        if pattern[(weeks + offset) % len(pattern)] == target:
            return d
        d += timedelta(weeks=1)

    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def find_zone_for_point(lat: float, lng: float) -> dict | None:
    """Return zone info for the given coordinate, or None if outside all zones."""
    seen: set[str] = set()
    matched: list[dict] = []

    for entry in ZONES:
        zid = entry["zone_id"]
        if zid in seen:
            continue
        if _ray_cast(lat, lng, entry["coordinates"]):
            seen.add(zid)
            matched.append({"zone_id": zid, "day": entry["day"]})

    if not matched:
        return None

    matched.sort(key=lambda e: _DAY_ORDER.index(e["day"]) if e["day"] in _DAY_ORDER else 99)

    primary  = matched[0]
    zone_id  = primary["zone_id"]
    schedule = _SCHEDULES.get(zone_id)

    return {
        "zone_id":        zone_id,
        "collection_day": primary["day"],
        "schedule":       schedule,
    }


def format_voice_prompt(zone: dict) -> str:
    day      = zone["collection_day"]
    schedule = zone.get("schedule")

    if not schedule:
        return (
            f"Your general waste red bin is collected every {day}. "
            "Your recycling yellow bin and garden waste green bin also go out on alternating fortnights."
        )

    today = date.today()
    next_recycling = _next_of_type(schedule, today, "recycling")
    next_garden    = _next_of_type(schedule, today, "garden")

    def fmt(d: date | None) -> str:
        return f"{d.day} {d.strftime('%B')}" if d else "not scheduled"

    return (
        f"Your general waste red bin is collected every {day}. "
        f"Your recycling yellow bin is next on {fmt(next_recycling)}. "
        f"Your garden waste green bin is next on {fmt(next_garden)}."
    )


# Load at import time
_load_zones()
_load_schedules()
