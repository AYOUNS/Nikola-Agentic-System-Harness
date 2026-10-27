---
name: outfit-advisor
description: Look up current conditions and today's hourly forecast (temperature, precipitation chance, windspeed) for any location within Germany, and turn that into a plain-language clothing recommendation like "should I wear a jacket?" — using the free no-API-key Open-Meteo API. Use whenever the user asks about weather, forecast, temperature, rain chance, or wind for a German place, asks whether they need a jacket/coat/umbrella (right now or at a specific time), wants to know conditions at a specific hour, or is building/debugging/extending a weather or outfit-recommendation tool, script, or MCP server. Also use if a call to the bundled weather client or clothing advisor fails or returns unexpected data, since this skill documents the request/response shape and common failure modes.
version: "1.0"
---

# Weather Lookup & Jacket Advisor (Germany)

Get current conditions and an hourly forecast for any location within
**Germany**, via [Open-Meteo](https://open-meteo.com) — free, no API
key, no signup, no rate-limit headaches for normal personal use — and
turn it into a "should I wear a jacket?" recommendation.

Geocoding is restricted to Germany (`countryCode=DE`); non-German place
names come back as not found. See `references/reference.md` if this
needs to be widened to other countries or made global later.

## Quick usage

The bundled script `scripts/weather_client.py` does the whole job.

**CLI:**
```bash
python scripts/weather_client.py Passau
python scripts/weather_client.py "Tokyo"
```

**As a library:**
```python
from weather_client import get_weather, get_hourly_forecast

# Current conditions + full hourly forecast for today
result = get_weather(location_name="Passau")
print(result.to_dict())

# Just one specific hour
point = get_hourly_forecast("Passau", "08:00")
print(point)  # HourlyPoint(time=..., temperature_c=..., precipitation_probability=..., windspeed_kmh=...)
```

`get_weather()` returns a `WeatherResult` dataclass with: `found`,
`location` (name/lat/lon/country/admin1), `current_time` (location-local
ISO timestamp), `current_temperature_c`, `current_windspeed_kmh`,
`hourly` (list of `HourlyPoint` — one per hour today), `error`.

**"Should I wear a jacket?"**
```bash
python scripts/clothing_advisor.py Passau            # right now
python scripts/clothing_advisor.py Passau 08:00      # at a specific hour today
```
```python
from clothing_advisor import get_jacket_recommendation

rec = get_jacket_recommendation("Passau")            # right now
rec = get_jacket_recommendation("Passau", hour="08:00")  # specific hour
print(rec.advice)
# "Yes — wear a jacket at 08:00. It'll be only 10°C and 55% chance of rain."
```
Returns a `JacketRecommendation` dataclass: `found`, `location`, `time`,
`temperature_c`, `precipitation_probability`, `windspeed_kmh`,
`jacket_recommended` (bool), `advice` (human-readable string), `error`.

A jacket is recommended if **any** threshold is crossed (tunable in
`clothing_advisor.py`):
```python
JACKET_TEMP_THRESHOLD_C = 12.0
RAIN_PROBABILITY_THRESHOLD = 40  # percent
WIND_THRESHOLD_KMH = 25.0
```

**Timezone correctness:** "right now" uses the *target location's* own
local time (Open-Meteo's `current_weather.time`), never the machine
running the script — so asking about Tokyo from a server in Germany
still checks Tokyo's actual current hour. See
`references/reference.md` for why this matters and how it's implemented.

Only dependency: `requests`. No API key, no environment variables.

## How it works

Two Open-Meteo endpoints, both free/keyless:

1. **Geocoding** — `https://geocoding-api.open-meteo.com/v1/search`
   resolves a place name (e.g. `"Passau"`) to lat/lon. Skipped entirely
   if you call `get_weather(latitude=..., longitude=...)` directly.
2. **Forecast** — `https://api.open-meteo.com/v1/forecast` returns
   current conditions plus hourly `temperature_2m`,
   `precipitation_probability`, and `windspeed_10m` for the target day
   (defaults to today).

## Embedding this in an existing MCP server

If the user already has an MCP server, they do **not** need a whole new
server — just:

1. Copy `scripts/weather_client.py` and `scripts/clothing_advisor.py`
   into their project (the latter imports from the former, so both are
   needed together).
2. Import `get_weather` and/or `get_jacket_recommendation`.
3. Register tool functions that call them and return `.to_dict()`.

```python
from weather_client import get_weather
from clothing_advisor import get_jacket_recommendation

@mcp.tool()
def check_weather(location: str) -> dict:
    """Get current conditions and today's hourly forecast for a location."""
    return get_weather(location_name=location).to_dict()

@mcp.tool()
def should_i_wear_a_jacket(location: str, hour: str = "") -> dict:
    """Should I wear a jacket? Checks weather right now or at a given hour."""
    return get_jacket_recommendation(location, hour=hour or None).to_dict()
```

## Testing an MCP server built from this

`mcp.run()` talks over stdio (JSON-RPC) and produces **no terminal
output by design** — that's not a bug. To actually see results:
- Test the logic directly: `python scripts/weather_client.py <location>`
  (a normal script, prints straight to the terminal).
- Test the MCP wiring: `npx @modelcontextprotocol/inspector python server.py`
  opens a local web UI to call the tool and see the JSON response.

## When results look wrong

If `found` is `False`:
- `"Could not find a location matching '...'"` → the geocoding search
  had no results. Try a more specific string (e.g. `"Springfield,
  Illinois"` instead of `"Springfield"`), or pass `latitude`/`longitude`
  directly if known.
- Any other error → see `references/reference.md` for the full error
  table and how to diagnose a broken parse (e.g. if Open-Meteo changes
  its response shape).

See `references/reference.md` for the full request/response schema,
field mapping, error modes, and extension notes (e.g. adding more
weather variables like humidity or UV index).
