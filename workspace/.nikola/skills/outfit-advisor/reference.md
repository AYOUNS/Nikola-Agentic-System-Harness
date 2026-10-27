# Weather Lookup — Reference

Deep-dive notes on `scripts/weather_client.py`: the endpoints, request
parameters, response shape, error modes, and how to extend it.

## Table of contents
- [The two endpoints](#the-two-endpoints)
- [Geocoding request/response](#geocoding-requestresponse)
- [Forecast request/response](#forecast-requestresponse)
- [Field mapping table](#field-mapping-table)
- [`WeatherResult` / `HourlyPoint` / `Location` schema](#weatherresult--hourlypoint--location-schema)
- [Error modes and what they mean](#error-modes-and-what-they-mean)
- [Diagnosing a broken parse](#diagnosing-a-broken-parse)
- [Timezones](#timezones)
- [Clothing advisor logic](#clothing-advisor-logic)
- [Extending: more variables, multi-day forecasts](#extending-more-variables-multi-day-forecasts)
- [Rate limiting](#rate-limiting)

## The two endpoints

Both are part of Open-Meteo's free public API — no key, no auth header,
no signup:

```
GET https://geocoding-api.open-meteo.com/v1/search   (name -> lat/lon)
GET https://api.open-meteo.com/v1/forecast            (lat/lon -> weather)
```

`get_weather(location_name=...)` calls geocoding first, then forecast.
`get_weather(latitude=..., longitude=...)` skips geocoding entirely.

## Geocoding request/response

**Request params** (`geocode()` in `weather_client.py`):
```python
{
    "name": location_name,
    "count": 1,
    "language": "en",
    "format": "json",
    "countryCode": "DE",   # restricts results to Germany
}
```
`count: 1` means only the single best match is requested — for
ambiguous names within Germany (e.g. two towns sharing a name) this
takes whichever Open-Meteo ranks first, which may not be the one the
user meant. `countryCode: "DE"` filters geocoding results to Germany
only — a search for a non-German place (e.g. "Tokyo") returns an empty
`results` list even though Open-Meteo's geocoding API itself is global.
To widen scope later, either drop the `countryCode` param entirely
(fully global) or make it a parameter of `geocode()`/`get_weather()`
instead of a hardcoded constant, so callers can pass a different
country or omit it.

**Response shape:**
```json
{
  "results": [
    {
      "name": "Passau",
      "latitude": 48.5665,
      "longitude": 13.4312,
      "country": "Germany",
      "admin1": "Bavaria"
    }
  ]
}
```
An empty or missing `"results"` list means no match — `geocode()`
returns `None`, and `get_weather()` surfaces this as `found: False`
with an error message.

## Forecast request/response

**Request params:**
```python
{
    "latitude": location.latitude,
    "longitude": location.longitude,
    "hourly": "temperature_2m,precipitation_probability,windspeed_10m",
    "current_weather": "true",
    "timezone": "auto",
    "start_date": target_day.isoformat(),
    "end_date": target_day.isoformat(),
}
```

**Response shape (relevant parts):**
```json
{
  "current_weather": {"temperature": 14.2, "windspeed": 9.0, "time": "..."},
  "hourly": {
    "time": ["2026-09-09T00:00", "2026-09-09T01:00", ...],
    "temperature_2m": [8.0, 7.5, ...],
    "precipitation_probability": [10, 12, ...],
    "windspeed_10m": [5.0, 4.5, ...]
  }
}
```
`hourly.time` is a list of ISO-format local timestamps (no timezone
suffix, since `timezone=auto` already localizes them to the location).
Each hourly array is parallel-indexed to `hourly.time` — index `i` in
`temperature_2m` corresponds to `hourly.time[i]`.

## Field mapping table

| `WeatherResult`/`HourlyPoint` field | Source JSON path |
|---|---|
| `current_temperature_c` | `current_weather.temperature` |
| `current_windspeed_kmh` | `current_weather.windspeed` |
| `hourly[i].time` | `hourly.time[i]` |
| `hourly[i].temperature_c` | `hourly.temperature_2m[i]` |
| `hourly[i].precipitation_probability` | `hourly.precipitation_probability[i]` |
| `hourly[i].windspeed_kmh` | `hourly.windspeed_10m[i]` |
| `location.name/latitude/longitude/country/admin1` | geocoding `results[0]` fields of the same name |

`get_hourly_forecast(location, hour, day=None)` builds a target
timestamp as `f"{day.isoformat()}T{hour[:2]}:00"` and does an exact
string match against `hourly.time` — so `hour` must be on-the-hour
(e.g. `"08:00"`, not `"08:15"`); Open-Meteo's default hourly resolution
doesn't have sub-hour entries.

## `WeatherResult` / `HourlyPoint` / `Location` schema

```python
@dataclass
class Location:
    name: str
    latitude: float
    longitude: float
    country: Optional[str]
    admin1: Optional[str]   # state/region

@dataclass
class HourlyPoint:
    time: str                                   # ISO "YYYY-MM-DDTHH:MM"
    temperature_c: Optional[float]
    precipitation_probability: Optional[int]     # percent
    windspeed_kmh: Optional[float]

@dataclass
class WeatherResult:
    found: bool
    location: Optional[Location]
    current_temperature_c: Optional[float]
    current_windspeed_kmh: Optional[float]
    hourly: list[HourlyPoint]
    error: Optional[str]
```

`.to_dict()` gives a plain JSON-serializable dict (used for MCP tool
returns).

## Error modes and what they mean

| Condition | `error` message | Likely cause / fix |
|---|---|---|
| No `location_name` or lat/lon given | "Provide either location_name or latitude/longitude." | Caller bug — always pass one or the other. |
| Geocoding found nothing | "Could not find '{name}' within Germany." | Name too ambiguous, misspelled, too obscure, or **outside Germany** — geocoding is scoped to `countryCode=DE` by design (see [Geocoding request/response](#geocoding-requestresponse)). Try a more specific German place name, or pass known lat/lon directly to bypass the country filter entirely. |
| Geocoding request failed | (returns `None` from `geocode()`, surfaces as the "could not find" message above) | Network issue, or Open-Meteo's geocoding endpoint down. |
| Forecast request failed | "Weather request failed: {exception}" | Network/timeout issue reaching `api.open-meteo.com`. |
| Forecast response not JSON | "Weather response wasn't valid JSON." | Unexpected — Open-Meteo should always return JSON on success; check for an HTTP error status not being raised. |

Note `resp.raise_for_status()` is called on both requests, so a non-2xx
HTTP response raises `requests.HTTPError`, caught and wrapped into the
"Weather request failed" message above.

## Diagnosing a broken parse

1. Run the CLI directly and inspect the full JSON:
   ```bash
   python scripts/weather_client.py "<location>"
   ```
2. If `hourly` is empty or fields are `null` where they shouldn't be,
   temporarily print the raw `resp.json()` inside `get_weather()` right
   after the forecast request, before any field extraction — this shows
   exactly what Open-Meteo returned.
3. Compare against the [field mapping table](#field-mapping-table) above
   and adjust the `hourly_raw.get(...)` keys in `get_weather()` if
   Open-Meteo has changed a field name (rare — this is a stable,
   versioned-ish public API, unlike scraped endpoints).

## Timezones

`timezone=auto` tells Open-Meteo to localize both `hourly.time` and
`current_weather.time` to the location's own timezone automatically —
so `"2026-09-09T08:00"` for Tokyo means 8am Tokyo time, not UTC, and not
the timezone of whatever machine is running the script. This matters
when combining a forecast with a local event time (e.g. "8am lecture")
— no manual timezone conversion needed as long as both are meant to be
local time at that location.

**This is why `clothing_advisor.py` never calls `datetime.now()` to
figure out "right now."** Doing so would use the *server's* local clock,
which is wrong the moment the target location is in a different
timezone (e.g. checking Tokyo's weather from a machine in Germany). The
correct approach — and what `get_jacket_recommendation()` does when no
`hour` is given — is:

1. Read `current_weather.time` from the forecast response (already
   location-local).
2. Round it down to the hour with `_round_down_to_hour()`.
3. Look up that exact timestamp in `hourly.time` to get temperature +
   precipitation probability + windspeed together (`current_weather`
   alone has no precipitation field — only `temperature` and
   `windspeed`).

If `current_time` is ever missing from a `WeatherResult` (shouldn't
happen since `current_weather=true` is always requested), the code
falls back to the server's own clock rather than failing outright — but
treat that as a bug worth investigating if it ever triggers.

## Clothing advisor logic

`clothing_advisor.get_jacket_recommendation(location_name, hour=None, day=None)`:

1. Calls `get_weather(location_name, day)` to get the full `WeatherResult`.
2. Determines the target timestamp:
   - If `hour` given: `f"{day.isoformat()}T{hour[:2]}:00"`.
   - If not: location-local "now", rounded to the hour (see
     [Timezones](#timezones) above).
3. Looks up that timestamp in `result.hourly` for temperature,
   precipitation probability, and windspeed together.
4. If no matching hourly point exists (target hour outside the fetched
   day's range), falls back to `current_temperature_c`/
   `current_windspeed_kmh` from `current_weather` — with no
   precipitation data, since that field isn't in `current_weather`.
5. Applies thresholds — jacket recommended if **any** are crossed:
   ```python
   JACKET_TEMP_THRESHOLD_C = 12.0
   RAIN_PROBABILITY_THRESHOLD = 40   # percent
   WIND_THRESHOLD_KMH = 25.0
   ```
6. Builds a one-line human-readable `advice` string listing which
   threshold(s) were crossed (e.g. "only 8°C and 70% chance of rain and
   windy (30 km/h)").

Returns a `JacketRecommendation` dataclass: `found`, `location`, `time`,
`temperature_c`, `precipitation_probability`, `windspeed_kmh`,
`jacket_recommended`, `advice`, `error`.

**Tuning:** all three thresholds are module-level constants in
`clothing_advisor.py` — adjust them directly for a warmer-blooded or
more rain-averse user, or split into per-condition recommendations
(e.g. "umbrella" vs "jacket") by checking `precipitation_probability`
and the temperature/wind conditions separately instead of OR-ing them
into one boolean.

## Extending: more variables, multi-day forecasts

- **More variables**: add to the `hourly` param string, e.g.
  `"temperature_2m,precipitation_probability,windspeed_10m,relative_humidity_2m,uv_index"`,
  then add matching fields to `HourlyPoint` and the extraction in
  `get_weather()`. Full variable list: Open-Meteo's docs at
  [open-meteo.com/en/docs](https://open-meteo.com/en/docs).
- **Multi-day forecasts**: currently `start_date`/`end_date` are both
  set to `target_day`, giving one day of hourly data. Set `end_date` to
  a later date (or use the `forecast_days` param instead) to get
  multiple days — `hourly.time` will just contain more entries spanning
  the requested range; existing parsing logic doesn't need to change.
- **Daily summaries instead of hourly**: swap the `hourly` param for
  `daily` (e.g. `"temperature_2m_max,temperature_2m_min,precipitation_sum"`)
  for a lighter-weight per-day response instead of per-hour.

## Rate limiting

No published hard rate limit for non-commercial/personal use, but the
client makes no attempt at retry/backoff — if you're calling this in a
loop (e.g. checking many locations), add your own delay between calls
rather than firing requests as fast as possible.
