"""
weather_client.py

Free, no-API-key weather lookup via Open-Meteo (https://open-meteo.com),
restricted to locations within Germany. Returns current conditions plus
an hourly forecast, so you can ask about "right now" or a specific
hour/day.

No signup, no API key, no rate-limit headaches for normal personal use.
"""

import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

GERMANY_COUNTRY_CODE = "DE"


@dataclass
class Location:
    name: str
    latitude: float
    longitude: float
    country: Optional[str] = None
    admin1: Optional[str] = None  # state/region, e.g. "Bavaria"


@dataclass
class HourlyPoint:
    time: str  # ISO "YYYY-MM-DDTHH:MM"
    temperature_c: Optional[float] = None
    precipitation_probability: Optional[int] = None
    windspeed_kmh: Optional[float] = None


@dataclass
class WeatherResult:
    found: bool
    location: Optional[Location] = None
    current_time: Optional[str] = None  # ISO "YYYY-MM-DDTHH:MM", local to the location
    current_temperature_c: Optional[float] = None
    current_windspeed_kmh: Optional[float] = None
    hourly: list[HourlyPoint] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "location": self.location.__dict__ if self.location else None,
            "current_time": self.current_time,
            "current_temperature_c": self.current_temperature_c,
            "current_windspeed_kmh": self.current_windspeed_kmh,
            "hourly": [h.__dict__ for h in self.hourly],
            "error": self.error,
        }


def geocode(location_name: str, timeout: int = 15) -> Optional[Location]:
    """
    Resolve a place name to coordinates via Open-Meteo's free geocoding
    API, restricted to locations within Germany (countryCode=DE).
    """
    try:
        resp = requests.get(
            GEOCODING_URL,
            params={
                "name": location_name,
                "count": 1,
                "language": "en",
                "format": "json",
                "countryCode": GERMANY_COUNTRY_CODE,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    results = data.get("results") or []
    if not results:
        return None

    r = results[0]
    return Location(
        name=r.get("name", location_name),
        latitude=r["latitude"],
        longitude=r["longitude"],
        country=r.get("country"),
        admin1=r.get("admin1"),
    )


def get_weather(
    location_name: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    day: Optional[date] = None,
    timeout: int = 15,
) -> WeatherResult:
    """
    Get current conditions + hourly forecast for a German location,
    either by name (geocoded automatically, restricted to Germany) or
    explicit lat/lon.

    Args:
        location_name: e.g. "Passau", "Munich", "Berlin".
        latitude/longitude: use instead of location_name to skip geocoding.
        day: date to fetch the hourly forecast for (defaults to today).
    """
    location: Optional[Location] = None

    if latitude is not None and longitude is not None:
        location = Location(name=location_name or f"{latitude},{longitude}", latitude=latitude, longitude=longitude)
    elif location_name:
        location = geocode(location_name, timeout=timeout)
        if location is None:
            return WeatherResult(
                found=False,
                error=f"Could not find '{location_name}' within Germany.",
            )
    else:
        return WeatherResult(found=False, error="Provide either location_name or latitude/longitude.")

    target_day = day or date.today()
    params = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "hourly": "temperature_2m,precipitation_probability,windspeed_10m",
        "current_weather": "true",
        "timezone": "auto",
        "start_date": target_day.isoformat(),
        "end_date": target_day.isoformat(),
    }

    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return WeatherResult(found=False, location=location, error=f"Weather request failed: {e}")
    except ValueError:
        return WeatherResult(found=False, location=location, error="Weather response wasn't valid JSON.")

    current = data.get("current_weather") or {}
    hourly_raw = data.get("hourly") or {}
    times = hourly_raw.get("time", [])
    temps = hourly_raw.get("temperature_2m", [])
    precip = hourly_raw.get("precipitation_probability", [])
    wind = hourly_raw.get("windspeed_10m", [])

    hourly_points = [
        HourlyPoint(
            time=t,
            temperature_c=temps[i] if i < len(temps) else None,
            precipitation_probability=precip[i] if i < len(precip) else None,
            windspeed_kmh=wind[i] if i < len(wind) else None,
        )
        for i, t in enumerate(times)
    ]

    return WeatherResult(
        found=True,
        location=location,
        current_time=current.get("time"),
        current_temperature_c=current.get("temperature"),
        current_windspeed_kmh=current.get("windspeed"),
        hourly=hourly_points,
    )


def get_hourly_forecast(location_name: str, hour: str, day: Optional[date] = None) -> Optional[HourlyPoint]:
    """Convenience: get just the forecast for one specific "HH:MM" hour."""
    result = get_weather(location_name=location_name, day=day)
    if not result.found:
        return None
    target_day = day or date.today()
    hh = hour.split(":")[0].zfill(2)
    target_time = f"{target_day.isoformat()}T{hh}:00"
    for point in result.hourly:
        if point.time == target_time:
            return point
    return None


if __name__ == "__main__":
    import json

    loc = sys.argv[1] if len(sys.argv) > 1 else "Passau"
    result = get_weather(location_name=loc)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
