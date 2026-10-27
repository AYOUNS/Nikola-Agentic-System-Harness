"""
clothing_advisor.py

Turns a weather lookup into a plain-language jacket recommendation —
answers "should I wear my jacket?" for right now, or for a specific
hour/day, at any location.

Built on top of weather_client.py (Open-Meteo, free, no API key).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from weather_client import get_weather, HourlyPoint, Location

# Simple, tweakable thresholds. A jacket is recommended if ANY is crossed.
JACKET_TEMP_THRESHOLD_C = 12.0
RAIN_PROBABILITY_THRESHOLD = 40  # percent
WIND_THRESHOLD_KMH = 25.0


@dataclass
class JacketRecommendation:
    found: bool
    location: Optional[Location] = None
    time: Optional[str] = None  # ISO "YYYY-MM-DDTHH:MM", local to the location
    temperature_c: Optional[float] = None
    precipitation_probability: Optional[int] = None
    windspeed_kmh: Optional[float] = None
    jacket_recommended: Optional[bool] = None
    advice: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "location": self.location.__dict__ if self.location else None,
            "time": self.time,
            "temperature_c": self.temperature_c,
            "precipitation_probability": self.precipitation_probability,
            "windspeed_kmh": self.windspeed_kmh,
            "jacket_recommended": self.jacket_recommended,
            "advice": self.advice,
            "error": self.error,
        }


def _round_down_to_hour(iso_time: str) -> str:
    """'2026-09-09T14:37' -> '2026-09-09T14:00'"""
    dt = datetime.fromisoformat(iso_time)
    return dt.replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes")


def _pick_hourly_point(hourly: list[HourlyPoint], target_time: str) -> Optional[HourlyPoint]:
    for point in hourly:
        if point.time == target_time:
            return point
    return None


def _build_advice_text(when_label: str, temp: Optional[float], precip: Optional[int], wind: Optional[float], jacket: bool) -> str:
    reasons = []
    if temp is not None and temp < JACKET_TEMP_THRESHOLD_C:
        reasons.append(f"only {temp:.0f}°C")
    if precip is not None and precip >= RAIN_PROBABILITY_THRESHOLD:
        reasons.append(f"{precip}% chance of rain")
    if wind is not None and wind >= WIND_THRESHOLD_KMH:
        reasons.append(f"windy ({wind:.0f} km/h)")

    if jacket:
        reason_text = " and ".join(reasons) if reasons else "cooler conditions"
        return f"Yes — wear a jacket {when_label}. It'll be {reason_text}."
    return f"No jacket needed {when_label} — conditions look mild and dry."


def get_jacket_recommendation(
    location_name: str,
    hour: Optional[str] = None,
    day: Optional[date] = None,
) -> JacketRecommendation:
    """
    Answer "should I wear a jacket?" for a location, either right now
    (default) or at a specific "HH:MM" hour on a given day.

    Args:
        location_name: e.g. "Passau", "Tokyo".
        hour: optional "HH:MM" 24h — if omitted, uses the current hour
              at that location.
        day: optional date — defaults to today.
    """
    result = get_weather(location_name=location_name, day=day)
    if not result.found:
        return JacketRecommendation(found=False, error=result.error)

    if hour:
        target_day = day or date.today()
        hh = hour.split(":")[0].zfill(2)
        target_time = f"{target_day.isoformat()}T{hh}:00"
        when_label = f"at {hh}:00"
    else:
        # "Now": use Open-Meteo's own current_weather.time, which is
        # already local to the target location (not the server running
        # this script) — critical for locations in other timezones.
        # Round down to the hour to match the hourly array's on-the-hour
        # timestamps.
        if result.current_time:
            target_time = _round_down_to_hour(result.current_time)
        else:
            # Extremely unlikely (current_weather=true was requested),
            # but fall back to the server's own clock rather than fail.
            target_time = datetime.now().replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
        when_label = "right now"

    point = _pick_hourly_point(result.hourly, target_time)
    if point is None:
        # Fall back to current_weather's own values (temperature/wind
        # only — Open-Meteo's current_weather block has no precipitation
        # probability field) if we couldn't line up an hourly entry.
        if result.current_temperature_c is not None:
            jacket = (
                result.current_temperature_c < JACKET_TEMP_THRESHOLD_C
                or (result.current_windspeed_kmh or 0) >= WIND_THRESHOLD_KMH
            )
            advice = _build_advice_text(when_label, result.current_temperature_c, None, result.current_windspeed_kmh, jacket)
            return JacketRecommendation(
                found=True,
                location=result.location,
                time=None,
                temperature_c=result.current_temperature_c,
                precipitation_probability=None,
                windspeed_kmh=result.current_windspeed_kmh,
                jacket_recommended=jacket,
                advice=advice,
            )
        return JacketRecommendation(
            found=False,
            location=result.location,
            error=f"No forecast data available for {target_time}.",
        )

    jacket = (
        (point.temperature_c is not None and point.temperature_c < JACKET_TEMP_THRESHOLD_C)
        or (point.precipitation_probability is not None and point.precipitation_probability >= RAIN_PROBABILITY_THRESHOLD)
        or (point.windspeed_kmh is not None and point.windspeed_kmh >= WIND_THRESHOLD_KMH)
    )
    advice = _build_advice_text(when_label, point.temperature_c, point.precipitation_probability, point.windspeed_kmh, jacket)

    return JacketRecommendation(
        found=True,
        location=result.location,
        time=point.time,
        temperature_c=point.temperature_c,
        precipitation_probability=point.precipitation_probability,
        windspeed_kmh=point.windspeed_kmh,
        jacket_recommended=jacket,
        advice=advice,
    )


if __name__ == "__main__":
    import json
    import sys

    loc = sys.argv[1] if len(sys.argv) > 1 else "Passau"
    hr = sys.argv[2] if len(sys.argv) > 2 else None
    result = get_jacket_recommendation(loc, hour=hr)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
