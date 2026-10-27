"""
dhl_scraper.py

Web-scraping based DHL tracking lookup — v2, using DHL's own internal
JSON endpoint directly via `requests` (no headless browser needed).

DHL's tracking frontend (dhl.de and dhl.com) calls a backend search
endpoint to populate the page. Since that endpoint returns plain JSON
with no auth/API key, we can call it directly instead of driving a full
browser (like the earlier Playwright-based version did). This is faster,
lighter, and doesn't need `playwright install chromium`.

Endpoint:
    GET https://www.dhl.de/int-verfolgen/data/search
        ?piececode=<TRACKING_NUMBER>
        &noRedirect=true
        &language=en

IMPORTANT — please read:
- This is an unofficial, undocumented endpoint. It's what DHL's own
  website calls internally, but it is NOT a supported/versioned public
  API — DHL can change its shape, add auth requirements, or rate-limit/
  block it at any time without notice.
- Respect DHL's Terms of Service for your use case; don't hammer this
  endpoint with high request volume.
- If this becomes unreliable, DHL's official "Shipment Tracking -
  Unified" API (free consumer key from developer.dhl.com) is the
  supported alternative with a documented, stable schema.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

SEARCH_URL = "https://www.dhl.de/int-verfolgen/data/search"

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://www.dhl.de/int-verfolgen",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


@dataclass
class TrackingEvent:
    timestamp: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


@dataclass
class TrackingResult:
    tracking_number: str
    found: bool
    status: Optional[str] = None
    status_description: Optional[str] = None
    estimated_delivery: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    events: list[TrackingEvent] = field(default_factory=list)
    source: Optional[str] = None  # "dhl_internal_api"
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tracking_number": self.tracking_number,
            "found": self.found,
            "status": self.status,
            "status_description": self.status_description,
            "estimated_delivery": self.estimated_delivery,
            "origin": self.origin,
            "destination": self.destination,
            "events": [e.__dict__ for e in self.events],
            "source": self.source,
            "error": self.error,
        }


def _first(*values):
    """Return the first non-None/non-empty value."""
    for v in values:
        if v:
            return v
    return None


def _dig(d: Any, *path, default=None):
    """Safely walk a nested dict/list path, returning `default` on any miss."""
    cur = d
    for key in path:
        if isinstance(key, int):
            if not isinstance(cur, list) or key >= len(cur):
                return default
            cur = cur[key]
        else:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
    return cur


def _find_shipment_block(payload: Any) -> Optional[dict]:
    """
    The exact top-level shape of this endpoint isn't publicly documented
    and can vary (a bare shipment object, a "sendungen"/"shipments" list,
    wrapped under "results", etc). Search a few likely spots rather than
    assuming one fixed path.
    """
    if not isinstance(payload, dict):
        return None

    candidates = [
        payload,
        _dig(payload, "sendungen", 0),
        _dig(payload, "shipments", 0),
        _dig(payload, "results", 0),
        _dig(payload, "data", "sendungen", 0),
        _dig(payload, "data", "shipments", 0),
    ]
    for c in candidates:
        if isinstance(c, dict) and any(
            k in c for k in ("status", "sendungsverlauf", "events", "sendungsdetails", "statusText")
        ):
            return c
    return None


def _extract_events(shipment: dict) -> list[TrackingEvent]:
    events_raw = _first(
        shipment.get("sendungsverlauf"),
        shipment.get("events"),
        _dig(shipment, "sendungsdetails", "sendungsverlauf"),
        [],
    )
    events = []
    for ev in events_raw or []:
        if not isinstance(ev, dict):
            continue
        events.append(
            TrackingEvent(
                timestamp=_first(ev.get("datum"), ev.get("date"), ev.get("timestamp")),
                location=_first(ev.get("ort"), ev.get("location")),
                description=_first(ev.get("status"), ev.get("description"), ev.get("text")),
            )
        )
    return events


def _extract_from_payload(payload: Any, tracking_number: str) -> TrackingResult:
    shipment = _find_shipment_block(payload)

    if shipment is None:
        return TrackingResult(
            tracking_number=tracking_number,
            found=False,
            error="Could not locate shipment data in the response — DHL may have changed the endpoint's shape, "
                  "or this tracking number returned no results. Run with raw=True to inspect the payload.",
            raw=payload if isinstance(payload, dict) else None,
        )

    status_desc = _first(
        shipment.get("statusText"),
        shipment.get("status"),
        _dig(shipment, "sendungsdetails", "statusText"),
    )

    result = TrackingResult(
        tracking_number=tracking_number,
        found=True,
        status=status_desc,
        status_description=status_desc,
        estimated_delivery=_first(
            shipment.get("voraussichtlicheZustellung"),
            shipment.get("estimatedDelivery"),
        ),
        origin=_first(
            _dig(shipment, "sendungsdetails", "herkunft"),
            shipment.get("origin"),
        ),
        destination=_first(
            _dig(shipment, "sendungsdetails", "empfaenger", "ort"),
            shipment.get("destination"),
        ),
        source="dhl_internal_api",
        raw=payload if isinstance(payload, dict) else None,
    )
    result.events = _extract_events(shipment)
    return result


def track_dhl_package(tracking_number: str, timeout: int = 15, raw: bool = False) -> TrackingResult:
    """
    Look up a DHL package's status by calling DHL's internal tracking
    search endpoint directly (no browser, no API key).

    Args:
        tracking_number: DHL piece/tracking code.
        timeout: request timeout in seconds.
        raw: if True, keep the full raw JSON payload on the result even
             when parsing succeeds (useful while you're confirming the
             schema against your own account/shipments).
    """
    tracking_number = tracking_number.strip()
    if not tracking_number or not re.match(r"^[A-Za-z0-9]{6,40}$", tracking_number):
        return TrackingResult(tracking_number=tracking_number, found=False, error="Invalid tracking number format.")

    params = {
        "piececode": tracking_number,
        "noRedirect": "true",
        "language": "en",
    }

    try:
        resp = requests.get(SEARCH_URL, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
    except requests.RequestException as e:
        return TrackingResult(tracking_number=tracking_number, found=False, error=f"Request failed: {e}")

    if resp.status_code == 404:
        return TrackingResult(tracking_number=tracking_number, found=False, error="Tracking number not found.")
    if resp.status_code == 429:
        return TrackingResult(tracking_number=tracking_number, found=False, error="Rate limited by DHL (HTTP 429). Slow down requests.")
    if resp.status_code != 200:
        return TrackingResult(
            tracking_number=tracking_number,
            found=False,
            error=f"Unexpected HTTP status {resp.status_code} from DHL.",
        )

    try:
        payload = resp.json()
    except ValueError:
        return TrackingResult(
            tracking_number=tracking_number,
            found=False,
            error="Response wasn't valid JSON — DHL may be returning an HTML challenge/block page instead of data.",
        )

    result = _extract_from_payload(payload, tracking_number)
    if not raw:
        result.raw = None
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dhl_scraper.py <TRACKING_NUMBER> [--raw]")
        sys.exit(1)

    show_raw = "--raw" in sys.argv
    res = track_dhl_package(sys.argv[1], raw=show_raw)
    print(json.dumps(res.to_dict() | ({"raw": res.raw} if show_raw else {}), indent=2, ensure_ascii=False))
