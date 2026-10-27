# DHL Tracking Scraper — Reference

Deep-dive notes on `scripts/dhl_scraper.py`: the endpoint, the JSON shape,
field mapping, error modes, and how to extend or repair this when DHL
changes something.

## Table of contents
- [The endpoint](#the-endpoint)
- [Request details](#request-details)
- [Response shape (known/expected)](#response-shape-knownexpected)
- [Field mapping table](#field-mapping-table)
- [`TrackingResult` / `TrackingEvent` schema](#trackingresult--trackingevent-schema)
- [Error modes and what they mean](#error-modes-and-what-they-mean)
- [Diagnosing a broken parse](#diagnosing-a-broken-parse)
- [Rate limiting and blocking](#rate-limiting-and-blocking)
- [Official API alternative](#official-api-alternative)
- [Extending to other carriers](#extending-to-other-carriers)
- [Things that are NOT supported by this script](#things-that-are-not-supported-by-this-script)

## The endpoint

```
GET https://www.dhl.de/int-verfolgen/data/search
```

This is DHL's own backend search endpoint — the same call their tracking
webpage (`dhl.de/en/privatkunden/dhl-sendungsverfolgung.html` style pages
that are *not* login-gated) makes internally to populate its JS UI.
Calling it directly skips rendering the page in a browser entirely.

It is **not** the same as:
- `dhl.com/global-en/home/tracking.html` — DHL's international tracking
  page, a heavier React SPA. Different site, different backend calls.
- `dhl.de/en/privatkunden/dhl-sendungsverfolgung.html` — DHL's
  **account-based** "My shipments" page. This one requires a logged-in
  session (you'll see login prompts / session-expiry messages if you
  fetch it without auth). This skill's script does **not** handle login
  and should not be extended to store/submit DHL account credentials —
  see [Things that are NOT supported](#things-that-are-not-supported-by-this-script).

## Request details

```python
params = {
    "piececode": tracking_number,   # the DHL tracking/waybill number
    "noRedirect": "true",
    "language": "en",
}
headers = {
    "Accept": "application/json",
    "Referer": "https://www.dhl.de/int-verfolgen",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
}
```

The `Referer` and `User-Agent` headers matter — without a browser-like
`User-Agent`, DHL may reject the request or return an HTML challenge page
instead of JSON (see [Error modes](#error-modes-and-what-they-mean)).

Tracking numbers are validated client-side with:
```python
re.match(r"^[A-Za-z0-9]{6,40}$", tracking_number)
```
Adjust this if you need to support formats outside 6-40 alphanumeric
characters (e.g. DHL Express uses 10-digit numeric waybills; DHL
Paket/eCommerce numbers are often longer alphanumeric strings).

## Response shape (known/expected)

**The exact schema is undocumented and not guaranteed stable.** The
parser was written defensively because the top-level wrapper key is
unconfirmed across all DHL product lines (Paket vs Express vs
international). It checks these candidate locations, in order, for a
dict that looks like shipment data:

```python
candidates = [
    payload,                                  # bare object at top level
    payload["sendungen"][0],                  # German: "shipments" list
    payload["shipments"][0],
    payload["results"][0],
    payload["data"]["sendungen"][0],
    payload["data"]["shipments"][0],
]
```

A candidate is accepted as "shipment data" if it contains any of:
`status`, `sendungsverlauf`, `events`, `sendungsdetails`, `statusText`.

If your real payload nests differently, add the correct path to this
list in `_find_shipment_block()`.

## Field mapping table

| `TrackingResult` field | Tried payload keys (in order) |
|---|---|
| `status` / `status_description` | `statusText` → `status` → `sendungsdetails.statusText` |
| `estimated_delivery` | `voraussichtlicheZustellung` → `estimatedDelivery` |
| `origin` | `sendungsdetails.herkunft` → `origin` |
| `destination` | `sendungsdetails.empfaenger.ort` → `destination` |
| `events[].timestamp` | `datum` → `date` → `timestamp` |
| `events[].location` | `ort` → `location` |
| `events[].description` | `status` → `description` → `text` |

The events list itself is read from (first match wins):
`sendungsverlauf` → `events` → `sendungsdetails.sendungsverlauf`.

German field names (`sendungsverlauf` = shipment history, `ort` =
location, `herkunft` = origin, `empfaenger` = recipient,
`voraussichtlicheZustellung` = estimated delivery) reflect that this is
the `dhl.de` (German-market) endpoint, even when `language=en` is passed
— the JSON *keys* stay German; only display text is localized.

## `TrackingResult` / `TrackingEvent` schema

```python
@dataclass
class TrackingEvent:
    timestamp: Optional[str]
    location: Optional[str]
    description: Optional[str]

@dataclass
class TrackingResult:
    tracking_number: str
    found: bool
    status: Optional[str]
    status_description: Optional[str]
    estimated_delivery: Optional[str]
    origin: Optional[str]
    destination: Optional[str]
    events: list[TrackingEvent]
    source: Optional[str]   # always "dhl_internal_api" on success
    error: Optional[str]
    raw: Optional[dict]     # only populated when raw=True is passed
```

`.to_dict()` gives a plain JSON-serializable dict (used for MCP tool
returns) — note it deliberately omits `raw` even when populated, to keep
tool responses compact; access `result.raw` directly if you need it.

## Error modes and what they mean

| Condition | `error` message | Likely cause / fix |
|---|---|---|
| Invalid format | "Invalid tracking number format." | Input didn't match `^[A-Za-z0-9]{6,40}$` — check for whitespace, dashes, or a format outside this range. |
| HTTP 404 | "Tracking number not found." | Number doesn't exist or DHL can't find it. |
| HTTP 429 | "Rate limited by DHL..." | Too many requests too fast — back off, add delay between calls. |
| Other non-200 | "Unexpected HTTP status {code} from DHL." | Could be a temporary DHL outage or a block. Log the status code. |
| Non-JSON response | "Response wasn't valid JSON — DHL may be returning an HTML challenge/block page..." | DHL is likely bot-blocking this request. Check if the `User-Agent` header needs updating, or if this IP/pattern got flagged. |
| Parsed but no shipment block found | "Could not locate shipment data in the response..." | Schema changed. Re-run with `raw=True` and inspect `result.raw` to find the real structure, then update `_find_shipment_block()`. |
| Network failure | "Request failed: {exception}" | DNS/connection/timeout issue — check network access to `dhl.de`. |

## Diagnosing a broken parse

1. Re-run with raw output:
   ```bash
   python scripts/dhl_scraper.py <TRACKING_NUMBER> --raw
   ```
2. Look at the printed `raw` payload — find the actual key names DHL
   used for status, events, origin/destination.
3. Update `_find_shipment_block()` (add the correct nesting path to
   `candidates`) and/or the key lookups in `_extract_from_payload()` /
   `_extract_events()` in `dhl_scraper.py` to match.
4. Re-run without `--raw` to confirm `found: true` and populated fields.

## Rate limiting and blocking

- No official rate limit is published for this endpoint since it's
  unofficial — treat it conservatively (e.g. a few requests per minute
  for personal/light use, not bulk polling).
- HTTP 429 responses are handled explicitly and surfaced as an `error`
  string — callers should back off, not retry immediately in a loop.
- Repeated automated traffic risks the IP or pattern getting blocked
  entirely (manifests as non-JSON/HTML challenge responses — see error
  table above).

## Official API alternative

If this endpoint starts breaking often, or the use case grows beyond
light/personal use, DHL's official **Shipment Tracking - Unified API**
is the supported path:

- Free Consumer Key from [developer.dhl.com](https://developer.dhl.com/api-reference/shipment-tracking)
- Documented, versioned JSON schema — won't silently change shape
- No risk of being treated as bot traffic

Swapping to it only requires replacing the body of
`track_dhl_package()` with an authenticated `requests.get()` call to the
official endpoint — `TrackingResult`, `.to_dict()`, and any MCP tool
wrapping this function stay unchanged.

## Extending to other carriers

Each carrier should get its own module mirroring this one's shape
(`ups_scraper.py`, `fedex_scraper.py`, ...), each exposing a
`track_<carrier>_package(tracking_number)` function returning the same
`TrackingResult`/`TrackingEvent` dataclasses (or carrier-specific
subclasses with the same core fields). This keeps any MCP server or
calling code carrier-agnostic — it can dispatch by carrier name and call
`.to_dict()` on whatever comes back.

## Things that are NOT supported by this script

- **DHL account login.** The account-based "My shipments" page requires
  an authenticated session. This script does not implement login and
  should not be extended to submit or store DHL usernames/passwords —
  if a user needs account-based tracking, treat that as a distinct,
  higher-risk feature request requiring careful credential handling
  (env vars, never hardcoded/logged), not a simple addition to this
  scraper.
- **Headless browser rendering.** Deliberately removed in favor of the
  direct JSON endpoint — do not reintroduce Playwright/Selenium here
  unless the JSON endpoint stops working entirely and no alternative
  exists.
- **Bulk/high-volume polling.** Not designed or intended for scraping at
  scale; see [Rate limiting](#rate-limiting-and-blocking).
