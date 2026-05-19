"""
Training-load metrics: TSS, Normalized Power, Intensity Factor,
mean-maximal power curve, Critical Power (Monod-Scherrer), Banister
CTL/ATL/TSB.

Algorithms are the Coggan / Banister / Monod-Scherrer originals as
implemented in GoldenCheetah (GPL). Pure-Python, no NumPy required —
keeps the dependency surface tiny.
"""
from __future__ import annotations

import math
from typing import Sequence


# ── Normalized Power (Coggan) ────────────────────────────────────────────

def normalized_power(power: Sequence[int | None], window: int = 30) -> int | None:
    """
    NP = (mean of (30-second-rolling-avg ^ 4)) ^ (1/4).

    Skips leading None values; treats interior None as 0. Returns None if
    there's no power data at all.
    """
    cleaned = [p if p is not None else 0 for p in power]
    if not cleaned or max(cleaned) == 0:
        return None
    rolled = _rolling_mean(cleaned, window)
    if not rolled:
        return None
    mean4 = sum(r ** 4 for r in rolled) / len(rolled)
    return int(round(mean4 ** 0.25))


def _rolling_mean(values: Sequence[float], window: int) -> list[float]:
    if len(values) < window:
        return []
    out: list[float] = []
    running = sum(values[:window])
    out.append(running / window)
    for i in range(window, len(values)):
        running += values[i] - values[i - window]
        out.append(running / window)
    return out


# ── TSS + IF ─────────────────────────────────────────────────────────────

def intensity_factor(np_val: int | None, ftp: int | None) -> float | None:
    if np_val is None or not ftp:
        return None
    return round(np_val / ftp, 3)


def tss(duration_seconds: int, np_val: int | None, ftp: int | None) -> float | None:
    """Coggan TSS = (sec * NP * IF) / (FTP * 3600) * 100."""
    if np_val is None or not ftp or duration_seconds <= 0:
        return None
    intf = np_val / ftp
    return round((duration_seconds * np_val * intf) / (ftp * 3600) * 100, 1)


def hr_tss(duration_seconds: int, avg_hr: int | None, lthr: int | None, max_hr: int | None) -> float | None:
    """
    Heart-rate-based TSS fallback (when no power available).
    Uses the standard hrTSS approximation: TRIMP-style with %LTHR scaling.
    """
    if not avg_hr or not lthr or duration_seconds <= 0:
        return None
    ratio = avg_hr / lthr
    # Simple piecewise approximation matching intervals.icu hrTSS shape
    if ratio < 0.69:
        intensity = ratio * 0.8
    elif ratio < 0.83:
        intensity = ratio * 0.9
    elif ratio < 0.97:
        intensity = ratio * 1.0
    else:
        intensity = ratio * 1.05
    return round((duration_seconds / 3600) * (intensity ** 2) * 100, 1)


# ── Mean-maximal power curve ─────────────────────────────────────────────

DEFAULT_CURVE_DURATIONS = (1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 5400, 7200)


def power_curve(power: Sequence[int | None], durations: Sequence[int] = DEFAULT_CURVE_DURATIONS) -> dict[int, int | None]:
    """
    For each duration t (seconds), return the max average power sustained
    for t consecutive seconds anywhere in the activity.
    """
    cleaned = [p if p is not None else 0 for p in power]
    if not cleaned:
        return {d: None for d in durations}
    out: dict[int, int | None] = {}
    for d in durations:
        if d > len(cleaned):
            out[d] = None
            continue
        rolled = _rolling_mean(cleaned, d)
        out[d] = int(round(max(rolled))) if rolled else None
    return out


# ── Critical Power (Monod-Scherrer two-parameter) ────────────────────────

def critical_power(curve: dict[int, int | None], min_t: int = 120, max_t: int = 1200) -> dict[str, float | None]:
    """
    Fit P(t) = CP + W' / t on points within [min_t, max_t] seconds.
    Linear regression on (1/t, P) — slope is W', intercept is CP.

    Returns {"cp": int_watts, "w_prime": int_joules} or {"cp": None, "w_prime": None}.
    """
    points = [(t, p) for t, p in curve.items() if p is not None and min_t <= t <= max_t]
    if len(points) < 2:
        return {"cp": None, "w_prime": None}
    xs = [1 / t for t, _ in points]
    ys = [p for _, p in points]
    n = len(points)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return {"cp": None, "w_prime": None}
    w_prime = num / den
    cp = mean_y - w_prime * mean_x
    if cp <= 0 or w_prime <= 0:
        return {"cp": None, "w_prime": None}
    return {"cp": int(round(cp)), "w_prime": int(round(w_prime))}


# ── Banister CTL / ATL / TSB ─────────────────────────────────────────────

def training_load(daily_tss: Sequence[float], ctl_days: int = 42, atl_days: int = 7) -> dict[str, list[float]]:
    """
    Banister exponentially-weighted moving averages.

    daily_tss: list of daily TSS values, oldest first, one entry per calendar
               day (use 0.0 for rest days — don't skip dates).
    Returns: {"ctl": [...], "atl": [...], "tsb": [...]} — same length as input.
    """
    if not daily_tss:
        return {"ctl": [], "atl": [], "tsb": []}
    k_ctl = 1 - math.exp(-1 / ctl_days)
    k_atl = 1 - math.exp(-1 / atl_days)
    ctl: list[float] = []
    atl: list[float] = []
    c = a = 0.0
    for stress in daily_tss:
        c = c + k_ctl * (stress - c)
        a = a + k_atl * (stress - a)
        ctl.append(round(c, 1))
        atl.append(round(a, 1))
    tsb = [round(ctl[i] - atl[i], 1) for i in range(len(ctl))]
    return {"ctl": ctl, "atl": atl, "tsb": tsb}


# ── Convenience: full analysis from a parsed activity ────────────────────

def analyze(activity: dict, ftp: int | None = None, lthr: int | None = None, max_hr: int | None = None) -> dict:
    """
    Take a parsed activity (from parsers.parse_activity) and return a flat
    dict of all computed metrics. Returns only what can actually be computed
    from the available streams.
    """
    streams = activity.get("streams", {})
    summary = activity.get("summary", {})
    duration = summary.get("duration_seconds", 0) or 0
    out: dict = {}

    power_stream = streams.get("power", [])
    if any(p for p in power_stream):
        np_val = normalized_power(power_stream)
        out["normalized_power"] = np_val
        if ftp:
            out["intensity_factor"] = intensity_factor(np_val, ftp)
            out["tss"] = tss(duration, np_val, ftp)
        curve = power_curve(power_stream)
        out["power_curve"] = {f"{d}s": v for d, v in curve.items() if v is not None}
        cp = critical_power(curve)
        if cp["cp"]:
            out["critical_power"] = cp["cp"]
            out["w_prime"] = cp["w_prime"]

    if "tss" not in out:
        out["hr_tss"] = hr_tss(duration, summary.get("avg_heart_rate"), lthr, max_hr)

    return out
