"""
weather.py — Open-Meteo weather fetching with 30-minute in-memory cache.

Open-Meteo is keyless; no API key required.
Default location: Sydney, Australia (-33.8688, 151.2093).
Lat/lon can be overridden via LAT / LON env vars.

Returned structure:
  {
    "tempC": float,
    "rainProbability": int,      # 0-100
    "condition": str,            # e.g. "Partly cloudy"
    "hourly": [
      {"hour": "09:00", "tempC": float, "rainMm": float}, ...  # next 24h
    ]
  }
"""

import os
import time
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LAT = float(os.getenv("LAT", "-33.8688"))
LON = float(os.getenv("LON", "151.2093"))
CACHE_TTL_SECONDS = 1800  # 30 minutes

_cache: Optional[dict] = None
_cache_ts: float = 0.0


# WMO weather interpretation codes → human-readable condition
_WMO_CONDITIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm with hail",
}


def get_weather() -> dict:
    """Return current weather, using cache if fresh. Never raises — returns empty dict on error."""
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is not None and (now - _cache_ts) < CACHE_TTL_SECONDS:
        return _cache

    try:
        result = _fetch_weather()
        _cache = result
        _cache_ts = now
        return result
    except Exception as e:
        logger.warning("Weather fetch failed: %s — returning cached or empty", e)
        return _cache or _empty_weather()


def _fetch_weather() -> dict:
    """Fetch current conditions + hourly forecast from Open-Meteo."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&current=temperature_2m,precipitation_probability,weathercode"
        f"&hourly=temperature_2m,precipitation,weathercode"
        f"&forecast_days=2"
        f"&timezone=auto"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    current = data.get("current", {})
    temp_c = round(current.get("temperature_2m", 0), 1)
    rain_prob = int(current.get("precipitation_probability", 0) or 0)
    wmo_code = int(current.get("weathercode", 0) or 0)
    condition = _WMO_CONDITIONS.get(wmo_code, "Unknown")

    # Build next-24h hourly strip
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precips = hourly.get("precipitation", [])

    from datetime import datetime
    # Open-Meteo returns naive local times (no timezone suffix) — compare against
    # naive local now to avoid the offset-naive vs offset-aware TypeError
    now_local = datetime.now()
    hourly_strip = []
    for i, t in enumerate(times[:48]):
        try:
            dt = datetime.fromisoformat(t)
        except ValueError:
            continue
        # Only include upcoming hours (next 24)
        if dt < now_local:
            continue
        hourly_strip.append({
            "hour": dt.strftime("%H:%M"),
            "tempC": round(float(temps[i]) if i < len(temps) else 0, 1),
            "rainMm": round(float(precips[i]) if i < len(precips) else 0.0, 2),
        })
        if len(hourly_strip) >= 24:
            break

    return {
        "tempC": temp_c,
        "rainProbability": rain_prob,
        "condition": condition,
        "hourly": hourly_strip,
    }


def _empty_weather() -> dict:
    return {"tempC": 0.0, "rainProbability": 0, "condition": "Unavailable", "hourly": []}
