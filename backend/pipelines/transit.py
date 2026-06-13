"""
pipelines/transit.py — P5: Transit alerts pipeline.

Polls TfNSW Open Data every 10 minutes between 05:00-23:00 local time.
Requires: TFNSW_API_KEY env var (optional — degrades gracefully).
Resolvers read from transit_alerts SQLite cache; this pipeline never touches request paths.

If TFNSW_API_KEY is absent, inserts a single "Normal Service" placeholder row
so the chip always renders something.
"""

import os
import logging
from datetime import datetime, timezone

import requests

import dashboard_db

logger = logging.getLogger(__name__)

TFNSW_API_KEY = os.getenv("TFNSW_API_KEY", "")

# TfNSW Trip Planner add_info endpoint (general service alerts)
TFNSW_ALERTS_URL = (
    "https://api.transport.nsw.gov.au/v1/tp/add_info"
    "?outputFormat=rapidJSON&coordOutputFormat=EPSG%3A4326&filterType=add_info"
    "&itdDate=today&itdTime=now&TfNSWTR=true"
)

# Lines to monitor — can be overridden via env (comma-separated)
_DEFAULT_LINES = {"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"}
MONITORED_LINES: set[str] = set(
    l.strip() for l in os.getenv("TRANSIT_LINES", "T1").split(",") if l.strip()
) or {"T1"}

FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")


async def run_pipeline(force: bool = False) -> None:
    """Fetch transit alerts and cache them in SQLite.

    Args:
        force: If True, bypass the 05:00-23:00 operating-hours guard.
               Used by startup and login triggers so the cache is always
               populated regardless of what time the server starts.
    """
    local_hour = datetime.now().hour
    in_hours = 5 <= local_hour < 23

    # Skip if outside operating hours — unless forced (startup / login trigger)
    if not FIXTURE_MODE and not force and not in_hours:
        logger.debug("Transit pipeline: outside operating hours (%d:xx), skipping", local_hour)
        return

    if FIXTURE_MODE:
        _load_fixture_alerts()
        return

    if not TFNSW_API_KEY:
        logger.warning("TFNSW_API_KEY not set — inserting Normal Service placeholder")
        _insert_normal_service()
        return

    try:
        alerts = _fetch_tfnsw_alerts()
        dashboard_db.replace_transit_alerts(alerts)
        logger.info("Transit pipeline: cached %d alerts", len(alerts))
    except Exception as e:
        logger.error("Transit pipeline error: %s", e, exc_info=True)
        # Don't clear existing cache on failure — stale is better than empty


def _fetch_tfnsw_alerts() -> list[dict]:
    """Fetch and normalise TfNSW service alerts for monitored lines."""
    resp = requests.get(
        TFNSW_ALERTS_URL,
        headers={"Authorization": f"apikey {TFNSW_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    infos = data.get("infos", {}).get("current", []) or []
    alerts: list[dict] = []

    for info in infos:
        # Extract affected lines
        item_elements = info.get("itemElements", [])
        affected_lines: set[str] = set()
        for el in item_elements:
            for prop in el.get("properties", {}).values():
                if isinstance(prop, str) and any(prop.startswith(l) for l in MONITORED_LINES):
                    affected_lines.add(prop.split(" ")[0])

        # If no specific line identified but content references our lines, include it
        content = info.get("content", "")
        for line in MONITORED_LINES:
            if line in content:
                affected_lines.add(line)

        if not affected_lines:
            continue

        # Determine severity
        priority = info.get("priority", "normal")
        if priority in ("high", "veryHigh") or "major" in content.lower():
            severity = "major"
        elif priority in ("medium",) or "minor" in content.lower() or "delay" in content.lower():
            severity = "minor"
        else:
            severity = "normal"

        for line in affected_lines:
            alerts.append({
                "line": line,
                "severity": severity,
                "title": info.get("subtitle") or content[:80],
                "detail": content[:500] if content else None,
                "starts_at": info.get("validFrom"),
                "ends_at": info.get("validTo"),
            })

    if not alerts:
        # All lines clear — insert a "Normal Service" row per monitored line
        return [_normal_service_alert(line) for line in sorted(MONITORED_LINES)]

    return alerts


def _normal_service_alert(line: str) -> dict:
    return {
        "line": line,
        "severity": "normal",
        "title": "Normal Service",
        "detail": None,
        "starts_at": None,
        "ends_at": None,
    }


def _insert_normal_service() -> None:
    alerts = [_normal_service_alert(line) for line in sorted(MONITORED_LINES)]
    dashboard_db.replace_transit_alerts(alerts)


def _load_fixture_alerts() -> None:
    """Load fixture data for dev/testing mode."""
    import json, os as _os
    fixture_path = _os.path.join(_os.path.dirname(__file__), "..", "fixtures", "transit.json")
    try:
        with open(fixture_path) as f:
            alerts = json.load(f)
        dashboard_db.replace_transit_alerts(alerts)
        logger.info("Transit pipeline: loaded %d fixture alerts", len(alerts))
    except FileNotFoundError:
        _insert_normal_service()
