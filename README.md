# gohybrid-mcp

> MCP server and Claude connector for fitness data — **intervals.icu** and **Strava**

Connect your training data to Claude in under 2 minutes. Works with Claude.ai (web), Claude Desktop, and any MCP-compatible client. Self-hostable, pip-installable, MIT licensed.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template)
&nbsp;
![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What it does

Gives Claude access to your fitness data through **32 tools**:

- **intervals.icu** — wellness (HRV, RHR, sleep, CTL/ATL/TSB), activities, HR/power/pace curves, training load, events, gear, weather
- **Strava** — activities, second-by-second streams, athlete stats, laps

Ask Claude things like:
- *"Am I overtraining this week?"*
- *"What was my best 1km effort in the last 30 days?"*
- *"How does my sleep score correlate with training load?"*
- *"Summarise my last 5 runs and suggest a recovery plan"*

---

## Quickstart — use the hosted connector

The fastest path. No install required.

1. Go to **[gohybrid-mcp.up.railway.app/connect](https://gohybrid-mcp.up.railway.app/connect)**
2. Enter your intervals.icu or Strava credentials → copy your `ghi_` token
3. In **Claude.ai → Settings → Integrations → Add custom integration**:
   - Server URL: `https://gohybrid-mcp.up.railway.app/mcp`
   - Header name: `Authorization`
   - Header value: `Bearer ghi_<your-token>`

---

## Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "gohybrid": {
      "url": "https://gohybrid-mcp.up.railway.app/mcp",
      "headers": {
        "Authorization": "Bearer ghi_<your-token>"
      }
    }
  }
}
```

Generate your `ghi_` token at `/connect` as above.

---

## Self-host on Railway

One-click deploy — no configuration needed:

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template)

Or manually:
1. Fork this repo
2. [Create a Railway project](https://railway.app) → Deploy from GitHub → select your fork
3. In Railway service settings → **Root Directory**: set to `gohybrid_mcp`
4. Deploy — Railway reads `Procfile` and `railway.toml` automatically
5. Your URL appears in the Railway dashboard

No environment variables required. All credentials arrive per-session via the `Authorization` header.

---

## Run locally

```bash
git clone https://github.com/AnshTanwar/gohybrid-mcp.git
cd gohybrid-mcp/gohybrid_mcp
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py --http
# Server at http://localhost:8000
# Token generator at http://localhost:8000/connect
```

Then add to Claude Desktop config pointing at `http://localhost:8000/mcp`.

---

## Install as a Python library

```bash
pip install git+https://github.com/AnshTanwar/gohybrid-mcp.git#subdirectory=gohybrid_mcp
```

### Embed in your FastAPI app

```python
from fastapi import FastAPI
from gohybrid_mcp import create_mcp_app, AuthMiddleware

app = FastAPI()
app.add_middleware(AuthMiddleware)          # reads Authorization: Bearer ghi_<token>
app.mount("/fitness", create_mcp_app())    # all 32 tools at /fitness/mcp
```

### Generate tokens programmatically

```python
from gohybrid_mcp import encode_token, decode_token

# intervals.icu
token = encode_token({"p": "intervals", "id": "i523248", "k": "your-api-key"})

# Strava (long-lived via refresh token)
token = encode_token({"p": "strava", "cid": "12345", "cs": "your-secret", "rt": "your-refresh-token"})
```

---

## Strava setup

Getting your Strava credentials:

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application (or use an existing one)
3. Copy **Client ID**, **Client Secret**, and **Refresh Token** — all visible on that page
4. Paste into [/connect](https://gohybrid-mcp.up.railway.app/connect) → Strava section

The refresh token doesn't expire (as long as you use the app occasionally). The server exchanges it for an access token on every request — you never need to refresh it manually.

> **No refresh token visible?** Click "Authorize" on the Strava API settings page to complete the OAuth flow once — the refresh token will then appear.

---

## intervals.icu setup

1. Go to [intervals.icu](https://intervals.icu) → Settings → Developer → **API key**
2. Copy your Athlete ID from the URL (`intervals.icu/athlete/i123456` → ID is `i123456`)
3. Paste both into [/connect](https://gohybrid-mcp.up.railway.app/connect)

---

## Token format

Tokens are `ghi_<base64url(JSON)>`. Stateless — no server-side storage.

```
intervals.icu: {"p":"intervals","id":"i123456","k":"your-api-key"}
Strava:        {"p":"strava","cid":"12345","cs":"secret","rt":"refresh-token"}
```

Treat your token like a password — anyone with it can read your fitness data.

---

## Tools reference

### intervals.icu tools (21)

| Tool | Description |
|---|---|
| `get_athlete_profile` | Name, weight, timezone, equipment |
| `get_sport_settings` | HR/power/pace zones and thresholds |
| `get_wellness(days)` | HRV, RHR, sleep, CTL/ATL/TSB per day |
| `get_wellness_for_date(date)` | Single-day wellness record |
| `get_training_summary(days)` | Averaged wellness + fitness model |
| `get_recent_activities(days)` | All activities with load, HR, distance |
| `get_activity_detail(id)` | Full metrics for one activity |
| `get_activity_streams(id)` | Second-by-second HR/pace/power/cadence |
| `get_activity_intervals(id)` | Detected intervals and lap stats |
| `get_activity_best_efforts(id)` | Peak efforts by duration |
| `get_activity_hr_curve(id)` | HR duration curve |
| `get_hr_histogram(id)` | Time in HR zones |
| `get_time_at_hr(id)` | Seconds at each BPM |
| `get_hr_curves(days)` | Best HR curves across all activities |
| `get_power_curves(sport, days)` | Best power duration curves |
| `get_power_vs_hr(id)` | Aerobic efficiency + cardiac drift |
| `get_power_hr_curve(sport, days)` | Efficiency trend over time |
| `get_mmp_model(sport)` | Critical power + W' model |
| `get_pace_curves(days)` | Best pace curves |
| `get_events(ahead, back)` | Calendar: workouts and races |
| `get_training_plan` | Current plan config |
| `get_gear` | Bikes and shoes with usage stats |
| `get_weather_forecast` | Forecast for your location |
| `get_activity_weather(id)` | Weather during a session |
| `get_athlete_summary` | Lifetime totals per sport |
| `search_activities(query)` | Find activities by name |

### Strava tools (6)

| Tool | Description |
|---|---|
| `get_strava_athlete` | Profile and equipment |
| `get_strava_activities(days)` | Activities with HR, load, distance |
| `get_strava_activity_detail(id)` | Full metrics + splits + segments |
| `get_strava_activity_streams(id)` | Second-by-second data |
| `get_strava_stats` | Lifetime + YTD + recent totals |
| `get_strava_activity_laps(id)` | Lap-by-lap breakdown |

---

## MCP discovery

This server publishes a machine-readable description at:
```
GET /.well-known/mcp/server-card.json
```

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Planned additions:**
- [ ] Whoop API (recovery, strain, HRV)
- [ ] Oura API (sleep stages, readiness score)
- [ ] Polar Accesslink
- [ ] Google Health Connect bridge
- [ ] Strava OAuth flow (one-click connect, no manual token)
- [ ] PyPI release

Open an issue to discuss a provider before building.

---

## License

MIT — see [LICENSE](LICENSE)
