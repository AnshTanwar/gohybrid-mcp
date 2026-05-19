"""
Parsers for activity files: FIT (Garmin/COROS/Wahoo/Polar/Suunto/Zepp/etc.),
TCX (Garmin/older devices), and GPX (any GPS device).

All three formats normalize into the same canonical dict so downstream
metrics code doesn't care about the source format.

Canonical activity:
    {
        "summary": {
            "sport": str,
            "start_time_iso": str,
            "duration_seconds": int,
            "distance_meters": float,
            "elevation_gain_meters": float,
            "avg_heart_rate": int | None,
            "max_heart_rate": int | None,
            "avg_power": int | None,
            "max_power": int | None,
            "avg_cadence": int | None,
            "avg_speed_kph": float | None,
            "calories": int | None,
        },
        "streams": {
            "time": [int, ...],          # seconds from activity start
            "power": [int, ...],         # watts, may be empty
            "heart_rate": [int, ...],    # bpm
            "cadence": [int, ...],       # rpm
            "speed": [float, ...],       # m/s
            "altitude": [float, ...],    # meters
            "distance": [float, ...],    # cumulative meters
            "lat": [float, ...],
            "lng": [float, ...],
        },
        "laps": [
            {"duration_seconds": int, "distance_meters": float,
             "avg_heart_rate": int | None, "avg_power": int | None,
             "avg_speed_kph": float | None},
        ],
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any
import xml.etree.ElementTree as ET


def parse_activity(content: bytes, file_type: str = "auto") -> dict[str, Any]:
    """Parse activity bytes into the canonical dict. file_type: fit | tcx | gpx | auto."""
    if file_type == "auto":
        file_type = _sniff_file_type(content)
    if file_type == "fit":
        return _parse_fit(content)
    if file_type == "tcx":
        return _parse_tcx(content)
    if file_type == "gpx":
        return _parse_gpx(content)
    raise ValueError(f"Unsupported file type: {file_type}")


def _sniff_file_type(content: bytes) -> str:
    head = content[:200].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<"):
        lower = head.lower()
        if b"<gpx" in lower:
            return "gpx"
        if b"<trainingcenterdatabase" in lower or b"<activities" in lower:
            return "tcx"
    if len(content) > 12 and content[8:12] == b".FIT":
        return "fit"
    raise ValueError("Could not detect file type (not FIT/TCX/GPX)")


# ── FIT ──────────────────────────────────────────────────────────────────

def _parse_fit(content: bytes) -> dict[str, Any]:
    from fitparse import FitFile  # imported lazily so module import is cheap

    fit = FitFile(BytesIO(content))

    streams: dict[str, list] = {
        "time": [], "power": [], "heart_rate": [], "cadence": [],
        "speed": [], "altitude": [], "distance": [], "lat": [], "lng": [],
    }
    start_ts: datetime | None = None

    for record in fit.get_messages("record"):
        values = {d.name: d.value for d in record}
        ts = values.get("timestamp")
        if ts is None:
            continue
        if start_ts is None:
            start_ts = ts
        streams["time"].append(int((ts - start_ts).total_seconds()))
        streams["power"].append(_int_or_none(values.get("power")))
        streams["heart_rate"].append(_int_or_none(values.get("heart_rate")))
        streams["cadence"].append(_int_or_none(values.get("cadence")))
        streams["speed"].append(_float_or_none(values.get("speed") or values.get("enhanced_speed")))
        streams["altitude"].append(_float_or_none(values.get("altitude") or values.get("enhanced_altitude")))
        streams["distance"].append(_float_or_none(values.get("distance")))
        lat = _semicircle_to_deg(values.get("position_lat"))
        lng = _semicircle_to_deg(values.get("position_long"))
        streams["lat"].append(lat)
        streams["lng"].append(lng)

    session = next(fit.get_messages("session"), None)
    summary = _summary_from_session(session, streams, start_ts)
    laps = [_lap_from_fit(lap) for lap in fit.get_messages("lap")]

    return {"summary": summary, "streams": _drop_empty_streams(streams), "laps": laps}


def _summary_from_session(session, streams: dict, start_ts: datetime | None) -> dict[str, Any]:
    if session is None:
        return _summary_from_streams(streams, start_ts)
    values = {d.name: d.value for d in session}
    return {
        "sport": values.get("sport") or _guess_sport(streams),
        "start_time_iso": _iso(start_ts),
        "duration_seconds": _int_or_none(values.get("total_timer_time") or values.get("total_elapsed_time")) or _streams_duration(streams),
        "distance_meters": _float_or_none(values.get("total_distance")) or _streams_distance(streams),
        "elevation_gain_meters": _float_or_none(values.get("total_ascent")) or _streams_elevation_gain(streams),
        "avg_heart_rate": _int_or_none(values.get("avg_heart_rate")) or _mean(streams["heart_rate"]),
        "max_heart_rate": _int_or_none(values.get("max_heart_rate")) or _max(streams["heart_rate"]),
        "avg_power": _int_or_none(values.get("avg_power")) or _mean(streams["power"]),
        "max_power": _int_or_none(values.get("max_power")) or _max(streams["power"]),
        "avg_cadence": _int_or_none(values.get("avg_cadence")) or _mean(streams["cadence"]),
        "avg_speed_kph": _ms_to_kph(_float_or_none(values.get("avg_speed")) or _mean(streams["speed"])),
        "calories": _int_or_none(values.get("total_calories")),
    }


def _lap_from_fit(lap) -> dict[str, Any]:
    v = {d.name: d.value for d in lap}
    return {
        "duration_seconds": _int_or_none(v.get("total_timer_time") or v.get("total_elapsed_time")),
        "distance_meters": _float_or_none(v.get("total_distance")),
        "avg_heart_rate": _int_or_none(v.get("avg_heart_rate")),
        "avg_power": _int_or_none(v.get("avg_power")),
        "avg_speed_kph": _ms_to_kph(_float_or_none(v.get("avg_speed"))),
    }


def _semicircle_to_deg(val) -> float | None:
    if val is None:
        return None
    return val * (180.0 / 2**31)


# ── TCX ──────────────────────────────────────────────────────────────────

_TCX_NS = {
    "tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
    "ext": "http://www.garmin.com/xmlschemas/ActivityExtension/v2",
}


def _parse_tcx(content: bytes) -> dict[str, Any]:
    root = ET.fromstring(content)
    activity = root.find(".//tcx:Activity", _TCX_NS)
    if activity is None:
        raise ValueError("TCX has no Activity element")

    sport = activity.get("Sport", "").lower() or "other"
    streams: dict[str, list] = {
        "time": [], "power": [], "heart_rate": [], "cadence": [],
        "speed": [], "altitude": [], "distance": [], "lat": [], "lng": [],
    }
    laps_out = []
    start_ts: datetime | None = None
    total_calories = 0
    total_distance = 0.0
    total_duration = 0.0

    for lap in activity.findall("tcx:Lap", _TCX_NS):
        lap_distance = float(_text(lap.find("tcx:DistanceMeters", _TCX_NS)) or 0)
        lap_duration = float(_text(lap.find("tcx:TotalTimeSeconds", _TCX_NS)) or 0)
        lap_cal = int(float(_text(lap.find("tcx:Calories", _TCX_NS)) or 0))
        total_distance += lap_distance
        total_duration += lap_duration
        total_calories += lap_cal
        laps_out.append({
            "duration_seconds": int(lap_duration),
            "distance_meters": lap_distance,
            "avg_heart_rate": _maybe_int(lap.find("tcx:AverageHeartRateBpm/tcx:Value", _TCX_NS)),
            "avg_power": None,
            "avg_speed_kph": _ms_to_kph(lap_distance / lap_duration) if lap_duration else None,
        })

        for tp in lap.findall(".//tcx:Trackpoint", _TCX_NS):
            ts = _parse_iso(_text(tp.find("tcx:Time", _TCX_NS)))
            if ts is None:
                continue
            if start_ts is None:
                start_ts = ts
            streams["time"].append(int((ts - start_ts).total_seconds()))
            streams["heart_rate"].append(_maybe_int(tp.find("tcx:HeartRateBpm/tcx:Value", _TCX_NS)))
            streams["cadence"].append(_maybe_int(tp.find("tcx:Cadence", _TCX_NS)))
            streams["altitude"].append(_maybe_float(tp.find("tcx:AltitudeMeters", _TCX_NS)))
            streams["distance"].append(_maybe_float(tp.find("tcx:DistanceMeters", _TCX_NS)))
            pos = tp.find("tcx:Position", _TCX_NS)
            if pos is not None:
                streams["lat"].append(_maybe_float(pos.find("tcx:LatitudeDegrees", _TCX_NS)))
                streams["lng"].append(_maybe_float(pos.find("tcx:LongitudeDegrees", _TCX_NS)))
            else:
                streams["lat"].append(None)
                streams["lng"].append(None)
            ext = tp.find("tcx:Extensions/ext:TPX", _TCX_NS)
            streams["power"].append(_maybe_int(ext.find("ext:Watts", _TCX_NS)) if ext is not None else None)
            streams["speed"].append(_maybe_float(ext.find("ext:Speed", _TCX_NS)) if ext is not None else None)

    summary = {
        "sport": sport,
        "start_time_iso": _iso(start_ts),
        "duration_seconds": int(total_duration) or _streams_duration(streams),
        "distance_meters": total_distance or _streams_distance(streams),
        "elevation_gain_meters": _streams_elevation_gain(streams),
        "avg_heart_rate": _mean(streams["heart_rate"]),
        "max_heart_rate": _max(streams["heart_rate"]),
        "avg_power": _mean(streams["power"]),
        "max_power": _max(streams["power"]),
        "avg_cadence": _mean(streams["cadence"]),
        "avg_speed_kph": _ms_to_kph(total_distance / total_duration) if total_duration else None,
        "calories": total_calories or None,
    }
    return {"summary": summary, "streams": _drop_empty_streams(streams), "laps": laps_out}


# ── GPX ──────────────────────────────────────────────────────────────────

def _parse_gpx(content: bytes) -> dict[str, Any]:
    """
    Parses GPX directly with ElementTree — gpxpy's extension support is unreliable
    across versions, but extensions (HR, power, cadence) are exactly what we need.
    """
    root = ET.fromstring(content)
    # Strip namespace prefixes from tags so we can match `trkpt`, `hr`, etc. directly.
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]

    sport = "other"
    type_el = root.find(".//trk/type")
    if type_el is not None and type_el.text:
        sport = type_el.text.lower()

    streams: dict[str, list] = {
        "time": [], "power": [], "heart_rate": [], "cadence": [],
        "speed": [], "altitude": [], "distance": [], "lat": [], "lng": [],
    }
    start_ts: datetime | None = None
    cum_distance = 0.0
    prev_lat = prev_lng = None

    for pt in root.iter("trkpt"):
        time_el = pt.find("time")
        ts = _parse_iso(_text(time_el))
        if ts is None:
            continue
        if start_ts is None:
            start_ts = ts
        try:
            lat = float(pt.get("lat"))
            lng = float(pt.get("lon"))
        except (TypeError, ValueError):
            continue
        if prev_lat is not None:
            cum_distance += _haversine_m(prev_lat, prev_lng, lat, lng)
        prev_lat, prev_lng = lat, lng

        streams["time"].append(int((ts - start_ts).total_seconds()))
        streams["lat"].append(lat)
        streams["lng"].append(lng)
        streams["distance"].append(cum_distance)
        streams["altitude"].append(_maybe_float(pt.find("ele")))

        hr = cad = power = None
        ext = pt.find("extensions")
        if ext is not None:
            for el in ext.iter():
                tag = el.tag.lower()
                if tag in ("hr", "heartrate"):
                    hr = _safe_int(el.text) or hr
                elif tag in ("cad", "cadence"):
                    cad = _safe_int(el.text) or cad
                elif tag in ("power", "watts"):
                    power = _safe_int(el.text) or power
        streams["heart_rate"].append(hr)
        streams["cadence"].append(cad)
        streams["power"].append(power)
        streams["speed"].append(None)

    summary = {
        "sport": sport,
        "start_time_iso": _iso(start_ts),
        "duration_seconds": _streams_duration(streams),
        "distance_meters": round(cum_distance, 1),
        "elevation_gain_meters": _streams_elevation_gain(streams),
        "avg_heart_rate": _mean(streams["heart_rate"]),
        "max_heart_rate": _max(streams["heart_rate"]),
        "avg_power": _mean(streams["power"]),
        "max_power": _max(streams["power"]),
        "avg_cadence": _mean(streams["cadence"]),
        "avg_speed_kph": _ms_to_kph(cum_distance / _streams_duration(streams)) if _streams_duration(streams) else None,
        "calories": None,
    }
    return {"summary": summary, "streams": _drop_empty_streams(streams), "laps": []}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters between two GPS points."""
    from math import radians, sin, cos, sqrt, asin
    R = 6_371_000.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * R * asin(sqrt(a))


# ── helpers ──────────────────────────────────────────────────────────────

def _summary_from_streams(streams: dict, start_ts) -> dict:
    return {
        "sport": _guess_sport(streams),
        "start_time_iso": _iso(start_ts),
        "duration_seconds": _streams_duration(streams),
        "distance_meters": _streams_distance(streams),
        "elevation_gain_meters": _streams_elevation_gain(streams),
        "avg_heart_rate": _mean(streams["heart_rate"]),
        "max_heart_rate": _max(streams["heart_rate"]),
        "avg_power": _mean(streams["power"]),
        "max_power": _max(streams["power"]),
        "avg_cadence": _mean(streams["cadence"]),
        "avg_speed_kph": _ms_to_kph(_mean(streams["speed"])),
        "calories": None,
    }


def _guess_sport(streams: dict) -> str:
    if any(p is not None and p > 0 for p in streams["power"]):
        return "cycling"
    return "running"


def _drop_empty_streams(streams: dict) -> dict:
    return {k: v for k, v in streams.items() if any(x is not None for x in v)}


def _streams_duration(streams: dict) -> int:
    return streams["time"][-1] if streams["time"] else 0


def _streams_distance(streams: dict) -> float:
    vals = [d for d in streams["distance"] if d is not None]
    return vals[-1] if vals else 0.0


def _streams_elevation_gain(streams: dict) -> float:
    alts = [a for a in streams["altitude"] if a is not None]
    if len(alts) < 2:
        return 0.0
    return sum(max(0, alts[i] - alts[i - 1]) for i in range(1, len(alts)))


def _mean(values: list) -> int | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return int(sum(nums) / len(nums))


def _max(values: list) -> int | None:
    nums = [v for v in values if v is not None]
    return max(nums) if nums else None


def _ms_to_kph(ms: float | None) -> float | None:
    return round(ms * 3.6, 1) if ms is not None else None


def _int_or_none(v) -> int | None:
    return int(v) if v is not None else None


def _float_or_none(v) -> float | None:
    return float(v) if v is not None else None


def _safe_int(s) -> int | None:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _text(el) -> str | None:
    return el.text if el is not None else None


def _maybe_int(el):
    return _safe_int(el.text) if el is not None and el.text is not None else None


def _maybe_float(el):
    if el is None or el.text is None:
        return None
    try:
        return float(el.text)
    except ValueError:
        return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
