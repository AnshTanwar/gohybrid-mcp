import os
import httpx
from datetime import date, timedelta
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

try:
    from .auth import get_creds, AuthMiddleware  # package import
except ImportError:
    from auth import get_creds, AuthMiddleware    # direct run

# DNS rebinding protection is off — clients authenticate via Bearer token,
# not browser cookies, so the attack vector doesn't apply.
mcp = FastMCP(
    "gohybrid-connector",
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_INTERVALS_BASE = "https://intervals.icu/api/v1"
_STRAVA_BASE = "https://www.strava.com/api/v3"


# ── Credential helpers ───────────────────────────────────────────────────────

def _intervals_creds() -> dict:
    c = get_creds()
    if c.get("p") != "intervals":
        raise RuntimeError(
            "This tool requires an intervals.icu token. "
            "Generate one at /connect using your Athlete ID and API Key."
        )
    return c


def _iath() -> str:
    """Current user's intervals.icu athlete ID."""
    return _intervals_creds()["id"]


def _iget(path: str, params: dict | None = None) -> dict | list:
    """GET from intervals.icu using session credentials."""
    c = _intervals_creds()
    r = httpx.get(f"{_INTERVALS_BASE}{path}", params=params, auth=("API_KEY", c["k"]), timeout=30)
    r.raise_for_status()
    return r.json()


def _strava_creds() -> dict:
    c = get_creds()
    if c.get("p") not in ("strava", "strava_oauth"):
        raise RuntimeError(
            "This tool requires a Strava token. "
            "Generate one at /connect using your Strava credentials."
        )
    return c


def _strava_access_token() -> str:
    """
    Exchange a refresh token for a short-lived access token.
    Three token shapes supported:
      p=strava_oauth: server-side OAuth app (one-click connect). cid/secret
                      live in env vars STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET.
      p=strava:       user's own Strava app (BYO). cid/cs/rt in the token.
      p=strava (legacy): {"t": "..."} — pre-refresh-flow short-lived token.
    """
    c = _strava_creds()

    if c.get("p") == "strava_oauth":
        client_id = os.environ.get("STRAVA_CLIENT_ID", "")
        client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError(
                "Server is not configured for Strava OAuth. "
                "Either set STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET, or use a BYO Strava token."
            )
        resp = httpx.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": c["rt"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    if "rt" in c:
        resp = httpx.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": c["cid"],
                "client_secret": c["cs"],
                "grant_type": "refresh_token",
                "refresh_token": c["rt"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    return c["t"]


def _sget(path: str, params: dict | None = None) -> dict | list:
    """GET from Strava API using session credentials."""
    token = _strava_access_token()
    r = httpx.get(
        f"{_STRAVA_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ═══════════════════════════════════════════════════════════════════════
# ATHLETE PROFILE & SETTINGS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_athlete_profile() -> dict:
    """
    Athlete profile: name, sex, city, timezone, weight, resting HR,
    bikes, shoes, and basic preferences.
    Use this to understand who the athlete is.
    """
    return _iget(f"/athlete/{_iath()}")


@mcp.tool(annotations={"readOnlyHint": True})
def get_sport_settings() -> list[dict]:
    """
    All sport-specific settings: HR zones, power zones, pace zones,
    thresholds (FTP, LTHR, max HR), warmup/cooldown times per sport.
    Sports configured: Ride, Run, Swim, Other.
    Use this when you need zone boundaries or threshold values.
    """
    return _iget(f"/athlete/{_iath()}/sport-settings")


# ═══════════════════════════════════════════════════════════════════════
# WELLNESS & RECOVERY
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_wellness(days: int = 14) -> list[dict]:
    """
    Daily wellness for the last N days. Each row includes:
    restingHR, hrv, sleepSecs, sleepScore, sleepQuality, steps, weight,
    spO2, stress, mood, fatigue, soreness, readiness,
    ctl (fitness), atl (fatigue), tsb (form), rampRate.
    Use this first for any 'how am I doing' question.
    Note: intervals.icu/Amazfit bug may shift HRV by one day.
    """
    end = date.today()
    start = end - timedelta(days=days)
    return _iget(
        f"/athlete/{_iath()}/wellness",
        {"oldest": start.isoformat(), "newest": end.isoformat()},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_wellness_for_date(date_str: str) -> dict:
    """
    Wellness record for a specific date (format: YYYY-MM-DD).
    Returns HRV, resting HR, sleep, weight, mood, readiness, etc.
    """
    return _iget(f"/athlete/{_iath()}/wellness/{date_str}")


@mcp.tool(annotations={"readOnlyHint": True})
def get_training_summary(days: int = 28) -> dict:
    """
    Computed summary of training trends over the last N days:
    average HRV, RHR, sleep, steps, weight, and current fitness model
    (CTL=fitness, ATL=fatigue, TSB=form).
    Use this to answer 'am I overtraining?' / 'am I recovering well?'.
    """
    end = date.today()
    start = end - timedelta(days=days)
    rows = _iget(
        f"/athlete/{_iath()}/wellness",
        {"oldest": start.isoformat(), "newest": end.isoformat()},
    )
    if not rows:
        return {"error": "no wellness data in range"}

    def avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "days": days,
        "avg_hrv": avg("hrv"),
        "avg_resting_hr": avg("restingHR"),
        "avg_sleep_hours": round((avg("sleepSecs") or 0) / 3600, 1) if avg("sleepSecs") else None,
        "avg_sleep_score": avg("sleepScore"),
        "avg_steps": avg("steps"),
        "avg_weight": avg("weight"),
        "latest_ctl": rows[-1].get("ctl"),
        "latest_atl": rows[-1].get("atl"),
        "latest_tsb": rows[-1].get("tsb"),
        "rows_returned": len(rows),
    }


# ═══════════════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_recent_activities(days: int = 90) -> list[dict]:
    """
    All activities in the last N days. Each includes:
    id, name, type, start_date_local, moving_time, distance,
    average_heartrate, max_heartrate, icu_training_load, calories.
    Activity types in data: Run, WeightTraining, Walk, Swim, OpenWaterSwim, VirtualRun.
    """
    end = date.today()
    start = end - timedelta(days=days)
    return _iget(
        f"/athlete/{_iath()}/activities",
        {"oldest": start.isoformat(), "newest": end.isoformat()},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_detail(activity_id: str) -> dict:
    """
    Full detail for one activity: all summary metrics, zone distribution,
    training load, pace, power, HR. Get activity_id from get_recent_activities.
    """
    return _iget(f"/activity/{activity_id}")


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_intervals(activity_id: str) -> dict:
    """
    Detected intervals/laps for one activity. Returns:
    - icu_intervals: list of intervals with per-interval stats
    - icu_groups: groupings of intervals
    Great for analyzing workout structure and interval quality.
    """
    return _iget(f"/activity/{activity_id}/intervals")


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_streams(activity_id: str, stream_types: str = "heartrate,velocity_smooth,cadence,watts,altitude") -> list[dict]:
    """
    Second-by-second data streams for one activity. Each stream has type, name, data[].
    Available stream types: time, watts, cadence, heartrate, distance, altitude,
    latlng, velocity_smooth, torque, fixed_altitude.
    Specify comma-separated types to limit response size.
    WARNING: can be large — filter to only needed streams.
    """
    return _iget(
        f"/activity/{activity_id}/streams",
        {"types": stream_types},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def search_activities(query: str) -> list[dict]:
    """
    Search activities by name or tag. Returns matching activities with
    id, name, start_date_local, type, distance, moving_time.
    """
    return _iget(
        f"/athlete/{_iath()}/activities/search",
        {"q": query},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_best_efforts(activity_id: str, stream: str = "heartrate", duration: int = 60) -> dict:
    """
    Best effort for a given duration (seconds) on a given stream within one activity.
    stream: 'heartrate', 'watts', 'cadence', 'velocity_smooth', 'altitude'
    duration: seconds (e.g. 60 = best 1-minute effort)
    Returns the peak sustained value for that duration.
    """
    return _iget(
        f"/activity/{activity_id}/best-efforts",
        {"stream": stream, "duration": str(duration)},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_map(activity_id: str) -> dict:
    """
    GPS map data for one activity: bounds, lat/lng track, route, and weather.
    Only available for outdoor activities with GPS.
    """
    return _iget(f"/activity/{activity_id}/map")


# ═══════════════════════════════════════════════════════════════════════
# HR ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_hr_curve(activity_id: str) -> dict:
    """
    Heart rate duration curve for one activity.
    Shows max sustained HR for durations from 1s to activity length.
    Contains: secs[] (durations) and values[] (peak HR at each duration).
    """
    return _iget(f"/activity/{activity_id}/hr-curve")


@mcp.tool(annotations={"readOnlyHint": True})
def get_hr_histogram(activity_id: str) -> list[dict]:
    """
    HR zone distribution for one activity.
    Shows time spent in each HR zone — useful for assessing intensity distribution.
    """
    return _iget(f"/activity/{activity_id}/hr-histogram")


@mcp.tool(annotations={"readOnlyHint": True})
def get_time_at_hr(activity_id: str) -> dict:
    """
    Detailed time-at-HR for one activity: seconds spent at each BPM value.
    Keys: max_bpm, min_bpm, secs[] (per-bpm breakdown), cumulative_secs[].
    """
    return _iget(f"/activity/{activity_id}/time-at-hr")


@mcp.tool(annotations={"readOnlyHint": True})
def get_hr_curves(days: int = 42) -> dict:
    """
    Best heart rate duration curves over the last N days across all activities.
    Shows peak sustained HR for various durations — aerobic capacity trend.
    """
    end = date.today()
    start = end - timedelta(days=days)
    return _iget(
        f"/athlete/{_iath()}/hr-curves",
        {"oldest": start.isoformat(), "newest": end.isoformat()},
    )


# ═══════════════════════════════════════════════════════════════════════
# POWER ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_power_curves(sport_type: str = "Run", days: int = 42) -> dict:
    """
    Best power duration curves over the last N days for a sport type.
    sport_type: 'Run', 'Ride', 'Swim', 'WeightTraining', etc.
    Shows peak power for durations from 1s to several hours.
    Use this to assess fitness changes or estimate FTP.
    """
    end = date.today()
    start = end - timedelta(days=days)
    return _iget(
        f"/athlete/{_iath()}/power-curves",
        {"oldest": start.isoformat(), "newest": end.isoformat(), "type": sport_type},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_mmp_model(sport_type: str = "Run") -> dict:
    """
    Critical power / MMP model for a sport type.
    Returns CP (critical power), W' (anaerobic work capacity), P-max.
    sport_type: 'Run', 'Ride', etc.
    """
    return _iget(
        f"/athlete/{_iath()}/mmp-model",
        {"type": sport_type},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_power_vs_hr(activity_id: str) -> dict:
    """
    Power vs heart rate correlation for one activity.
    Returns: decoupling %, power-HR buckets for first/second half,
    median cadence in Z2. Useful for aerobic efficiency and cardiac drift.
    """
    return _iget(f"/activity/{activity_id}/power-vs-hr")


@mcp.tool(annotations={"readOnlyHint": True})
def get_power_hr_curve(sport_type: str = "Run", days: int = 90) -> dict:
    """
    Power vs HR curve over a date range for a sport type.
    Shows aerobic efficiency trend — how much power per heartbeat over time.
    sport_type: 'Run', 'Ride'
    """
    end = date.today()
    start = end - timedelta(days=days)
    return _iget(
        f"/athlete/{_iath()}/power-hr-curve",
        {"start": start.isoformat(), "end": end.isoformat(), "type": sport_type},
    )


# ═══════════════════════════════════════════════════════════════════════
# PACE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_pace_curves(days: int = 42) -> dict:
    """
    Best pace curves over the last N days for running/swimming.
    Shows fastest pace sustained for various distances.
    """
    end = date.today()
    start = end - timedelta(days=days)
    return _iget(
        f"/athlete/{_iath()}/pace-curves",
        {"oldest": start.isoformat(), "newest": end.isoformat()},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_pace_curve(activity_id: str) -> dict:
    """
    Pace duration curve for one activity (running/swimming).
    Shows best pace sustained for various durations within the workout.
    """
    return _iget(f"/activity/{activity_id}/pace-curve")


# ═══════════════════════════════════════════════════════════════════════
# EVENTS & CALENDAR
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_events(days_ahead: int = 14, days_back: int = 7) -> list[dict]:
    """
    Calendar events: planned workouts, races, notes.
    Default: 7 days back + 14 days ahead.
    Use to see what's scheduled and compare plan vs actual.
    """
    start = date.today() - timedelta(days=days_back)
    end = date.today() + timedelta(days=days_ahead)
    return _iget(
        f"/athlete/{_iath()}/events",
        {"oldest": start.isoformat(), "newest": end.isoformat()},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_training_plan() -> dict:
    """
    Current training plan configuration: plan name, start date, last applied,
    timezone, and plan details.
    """
    return _iget(f"/athlete/{_iath()}/training-plan")


# ═══════════════════════════════════════════════════════════════════════
# GEAR & EQUIPMENT
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_gear() -> list[dict]:
    """
    All gear: bikes, shoes, etc. with usage stats
    (distance, time, activity count) and component tracking.
    """
    return _iget(f"/athlete/{_iath()}/gear")


# ═══════════════════════════════════════════════════════════════════════
# WEATHER
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_weather_forecast() -> dict:
    """
    Weather forecast for the athlete's configured location.
    Use when planning outdoor sessions or advising on hydration/clothing.
    """
    return _iget(f"/athlete/{_iath()}/weather-forecast")


@mcp.tool(annotations={"readOnlyHint": True})
def get_activity_weather(activity_id: str) -> dict:
    """
    Weather during a specific activity: temperature, humidity, wind speed/gust,
    apparent wind, precipitation. Useful for contextualizing performance.
    """
    return _iget(f"/activity/{activity_id}/weather-summary")


# ═══════════════════════════════════════════════════════════════════════
# ATHLETE SUMMARY
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_athlete_summary() -> list[dict]:
    """
    High-level training totals by sport type: count, time, moving_time,
    calories, elevation, training_load, distance, eFTP.
    Quick overview of training volume across all sports.
    """
    return _iget(f"/athlete/{_iath()}/athlete-summary")


# ═══════════════════════════════════════════════════════════════════════
# STRAVA
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_strava_athlete() -> dict:
    """
    Authenticated Strava athlete profile: name, city, country, weight,
    follower/friend counts, equipment (bikes, shoes).
    Use to understand who the athlete is.
    """
    return _sget("/athlete")


@mcp.tool(annotations={"readOnlyHint": True})
def get_strava_activities(days: int = 30) -> list[dict]:
    """
    All Strava activities in the last N days. Each includes:
    id, name, type, start_date_local, distance (metres), moving_time (secs),
    average_heartrate, max_heartrate, total_elevation_gain, average_cadence,
    average_watts, suffer_score, trainer, commute.
    Activity types: Run, Ride, Swim, Walk, WeightTraining, etc.
    """
    import time as _time
    after = int(_time.mktime((date.today() - timedelta(days=days)).timetuple()))
    return _sget("/athlete/activities", {"after": after, "per_page": 200})


@mcp.tool(annotations={"readOnlyHint": True})
def get_strava_activity_detail(activity_id: str) -> dict:
    """
    Full detail for one Strava activity: all summary metrics,
    segment efforts, best efforts, splits (km and mile), laps, gear used.
    Get activity_id from get_strava_activities.
    """
    return _sget(f"/activities/{activity_id}")


@mcp.tool(annotations={"readOnlyHint": True})
def get_strava_activity_streams(
    activity_id: str,
    stream_types: str = "heartrate,velocity_smooth,cadence,watts,altitude,distance",
) -> list[dict]:
    """
    Second-by-second data streams for one Strava activity.
    stream_types: comma-separated from heartrate, velocity_smooth, cadence,
    watts, altitude, distance, latlng, time, moving, grade_smooth.
    WARNING: can be large — only request needed streams.
    """
    return _sget(
        f"/activities/{activity_id}/streams",
        {"keys": stream_types, "key_by_type": "true"},
    )


@mcp.tool(annotations={"readOnlyHint": True})
def get_strava_stats() -> dict:
    """
    Athlete statistics: lifetime totals, year-to-date, and recent (4 weeks)
    for Run, Ride, Swim. Each bucket: count, distance, moving_time,
    elapsed_time, elevation_gain.
    """
    athlete = _sget("/athlete")
    return _sget(f"/athletes/{athlete['id']}/stats")


@mcp.tool(annotations={"readOnlyHint": True})
def get_strava_activity_laps(activity_id: str) -> list[dict]:
    """
    Lap/segment breakdowns for one Strava activity.
    Each lap: distance, elapsed_time, average_speed, average_heartrate,
    max_heartrate, average_cadence, average_watts.
    Useful for analysing interval structure and pacing consistency.
    """
    return _sget(f"/activities/{activity_id}/laps")


# ═══════════════════════════════════════════════════════════════════════
# FILE-BASED ACTIVITY ANALYSIS (works for any device — no auth required)
# ═══════════════════════════════════════════════════════════════════════

try:
    from .parsers import parse_activity as _parse_activity
    from .metrics import analyze as _analyze_metrics, training_load as _training_load
except ImportError:
    from parsers import parse_activity as _parse_activity
    from metrics import analyze as _analyze_metrics, training_load as _training_load


@mcp.tool(annotations={"readOnlyHint": True})
def analyze_activity_file(url: str, ftp: int | None = None, lthr: int | None = None) -> dict:
    """
    Parse a FIT, TCX, or GPX activity file from a URL and compute training metrics.

    Works for ANY device that exports activity files: Garmin, COROS, Wahoo,
    Polar, Suunto, Zepp/Amazfit, etc. — no integration required.

    Args:
        url: HTTPS URL to a .fit / .tcx / .gpx file (Dropbox, Google Drive,
             tmpfiles.org, S3, etc.). File type is auto-detected.
        ftp: Functional Threshold Power in watts. Required to compute TSS,
             Normalized Power, Intensity Factor. Skip if you don't know it.
        lthr: Lactate Threshold HR. Enables hrTSS fallback when there's no
              power data.

    Returns activity summary (sport, duration, distance, HR/power averages,
    elevation gain, laps) plus computed metrics (NP, TSS, IF, power curve,
    critical power, W'). Streams (per-second arrays) are NOT returned to
    keep response size sane — use get_activity_streams from intervals.icu/Strava
    if you need raw streams.

    WARNING: file must be <50MB.
    """
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    if len(resp.content) > 50 * 1024 * 1024:
        raise ValueError("file too large (>50MB)")

    parsed = _parse_activity(resp.content)
    metrics = _analyze_metrics(parsed, ftp=ftp, lthr=lthr)

    return {
        "summary": parsed["summary"],
        "laps": parsed["laps"],
        "metrics": metrics,
        "stream_keys_available": sorted(parsed["streams"].keys()),
        "sample_count": len(parsed["streams"].get("time", [])),
    }


@mcp.tool(annotations={"readOnlyHint": True})
def compute_training_load(daily_tss: list[float], ctl_days: int = 42, atl_days: int = 7) -> dict:
    """
    Compute Banister CTL (Fitness), ATL (Fatigue), and TSB (Form) from a
    list of daily TSS values.

    Use this when you have TSS data from multiple sources (e.g. mixing
    intervals.icu activities + Strava activities + uploaded files) and
    want a unified training-load curve.

    Args:
        daily_tss: list of daily TSS values, oldest first, ONE PER CALENDAR DAY.
                   Use 0.0 for rest days — don't skip dates.
        ctl_days: time constant for chronic load (default 42 = Coggan standard)
        atl_days: time constant for acute load (default 7 = Coggan standard)

    Returns: {"ctl": [...], "atl": [...], "tsb": [...]} — same length as input.
    Latest values are ctl[-1], atl[-1], tsb[-1].

    TSB interpretation:
      > +25  : Detraining (form too high, fitness dropping)
      +5..+25: Fresh, race-ready
      -10..+5: Maintaining
      -30..-10: Building (productive overload)
      < -30  : Overreaching / risk zone
    """
    return _training_load(daily_tss, ctl_days=ctl_days, atl_days=atl_days)


def create_mcp_app():
    """
    Return the raw MCP ASGI app — use this to mount GoHybrid fitness tools
    inside your own FastAPI / Starlette application.

    Example::

        from fastapi import FastAPI
        from gohybrid_mcp import create_mcp_app, AuthMiddleware

        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        app.mount("/fitness", create_mcp_app())
    """
    return mcp.streamable_http_app()


def create_server_app():
    """
    Return a fully-configured FastAPI app ready for deployment.
    Includes /mcp, /connect (token generator), /health, and
    /.well-known/mcp/server-card.json (MCP discovery) endpoints.
    AuthMiddleware is already applied.
    """
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.responses import FileResponse as _FileResponse, JSONResponse

    _HERE = os.path.dirname(os.path.abspath(__file__))

    # Initialize the MCP sub-app once so we can access its session manager.
    mcp_asgi = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp._session_manager.run():
            yield

    app = FastAPI(title="GoHybrid MCP Connector", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(AuthMiddleware)

    try:
        from .oauth import router as oauth_router
    except ImportError:
        from oauth import router as oauth_router
    app.include_router(oauth_router)

    @app.get("/connect", include_in_schema=False)
    async def connect_page():
        return _FileResponse(os.path.join(_HERE, "connect.html"))

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/.well-known/mcp/server-card.json", include_in_schema=False)
    async def server_card():
        return JSONResponse({
            "name": "GoHybrid MCP",
            "description": "Fitness data MCP server — intervals.icu and Strava",
            "version": "0.1.0",
            "mcp_endpoint": "/mcp",
            "connect_page": "/connect",
            "auth": {
                "type": "bearer",
                "token_format": "ghi_<base64url(json)>",
                "generate_at": "/connect",
            },
            "providers": ["intervals.icu", "strava"],
            "tool_count": 32,
            "repository": "https://github.com/AnshTanwar/gohybrid-mcp",
            "license": "MIT",
        })

    # Mount at root so the MCP sub-app's /mcp route is reachable at /mcp.
    # The session manager lifespan is wired into FastAPI's lifespan above.
    app.mount("/", mcp_asgi)
    return app


if __name__ == "__main__":
    import sys
    import uvicorn

    if "--http" in sys.argv:
        app = create_server_app()
        port = int(os.environ.get("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")
