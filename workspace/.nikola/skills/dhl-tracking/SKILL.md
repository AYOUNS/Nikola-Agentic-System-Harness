---
name: dhl-tracking
description: Check DHL package delivery status by tracking number using DHL's unofficial internal JSON endpoint (no API key, no browser). Use whenever the user asks to look up, check, or track a DHL shipment/package/parcel, wants delivery status for a DHL tracking/waybill number, or is building/debugging/extending a DHL tracking tool, script, or MCP server. Also use if a call to the bundled scraper starts failing or returns unexpected data (e.g. "could not locate shipment data"), since this skill documents the JSON response shape and how to fix the field mapping.
version: "1.0"
---

# DHL Package Tracking

Look up DHL shipment status by calling DHL's own internal tracking search
endpoint directly with `requests` — no API key, no headless browser, no
login required.

## Quick usage

The bundled script `scripts/dhl_scraper.py` does the whole job. Run it
directly or import it.

**CLI:**
```bash
python scripts/dhl_scraper.py <TRACKING_NUMBER>
python scripts/dhl_scraper.py <TRACKING_NUMBER> --raw   # also dump DHL's raw JSON
```

**As a library:**
```python
from dhl_scraper import track_dhl_package

result = track_dhl_package("990048774531")
print(result.to_dict())
```

`track_dhl_package()` returns a `TrackingResult` dataclass with:
`tracking_number`, `found`, `status`, `status_description`,
`estimated_delivery`, `origin`, `destination`, `events` (list of
`TrackingEvent`), `source`, `error`.

Only dependency: `requests`. No Playwright/Selenium, no API key.

## How it works

DHL's tracking frontend calls this backend endpoint to populate the page:

```
GET https://www.dhl.de/int-verfolgen/data/search
    ?piececode=<TRACKING_NUMBER>
    &noRedirect=true
    &language=en
```

It returns plain JSON with no auth needed — just a browser-like
`User-Agent` and `Referer` header (already set in `DEFAULT_HEADERS`).
The script calls this directly instead of rendering the page in a
browser.

## Embedding this in an existing MCP server

If the user already has an MCP server, they do **not** need a whole new
server — just:

1. Copy `scripts/dhl_scraper.py` into their project.
2. Import `track_dhl_package` from it.
3. Register one `@mcp.tool()` (or equivalent) function that calls it and
   returns `.to_dict()`.

```python
from dhl_scraper import track_dhl_package

@mcp.tool()
def track_dhl_shipment(tracking_number: str) -> dict:
    """Check the delivery status of a DHL package by its tracking number."""
    return track_dhl_package(tracking_number).to_dict()
```

## When results look wrong

If `found` is `False` with the error *"Could not locate shipment data in
the response"*, DHL likely changed the JSON shape. Read
`references/reference.md` for the known field-name mapping and how to
fix `_find_shipment_block()` / `_extract_from_payload()` in
`dhl_scraper.py` — re-run with `--raw` first to see DHL's actual payload.

## Important limitations — always mention these to the user

- This is an **unofficial, undocumented** endpoint — not a stable public
  API contract. DHL can change its shape, add auth, or block/rate-limit
  it at any time without notice.
- Don't hammer it with high request volume — that's the fastest way to
  get rate-limited (HTTP 429) or blocked.
- Automated calls to DHL's internal endpoints may conflict with DHL's
  Terms of Service depending on jurisdiction/use case — flag this if the
  user's use case goes beyond personal/light use.
- A supported alternative exists: DHL's free official **Shipment
  Tracking - Unified API** (Consumer Key from developer.dhl.com). See
  `references/reference.md` for when to recommend switching to it.

See `references/reference.md` for the full JSON schema notes, field
mapping table, error handling details, and extension guidance (e.g.
adding other carriers).
