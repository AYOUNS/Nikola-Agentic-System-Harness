"""Calendar tools backed by a single iCalendar (.ics) file.

Provides async implementations of: current_datetime, list_events, find_free_slots,
create_event, update_event.

Extracted from agent harness and refactored to async for MCP server use.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

import aiofiles
from icalendar import Calendar, Event

# Constants
DEFAULT_DAY_START = "09:00"
DEFAULT_DAY_END = "18:00"
_PRODID = "-//Nikola Agent Harness//Group 3//EN"


def get_calendar_path() -> Path:
    """Resolve the .ics file path from AGENT_WORKSPACE env var.
    
    Calendar lives at {AGENT_WORKSPACE}/calendar.ics.
    
    Returns:
        Resolved absolute Path. The file need not exist; create_event creates it.
    """
    workspace = os.environ.get("AGENT_WORKSPACE", "/workspace")
    return Path(workspace) / "calendar.ics"


def parse_datetime(value: str) -> datetime:
    """Parse a datetime written the way a model tends to write one.

    Accepts "2026-09-10T14:00", "2026-09-10 14:00", seconds optional, and a bare
    "2026-09-10" meaning midnight. Anything else raises rather than guessing, because a
    silently misread date produces a plausible-looking event on the wrong day.

    Args:
        value: the datetime string.

    Returns:
        A naive datetime.

    Raises:
        ValueError: the string matches none of the accepted shapes.
    """
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot read '{value}' as a date/time. Use YYYY-MM-DD HH:MM.")


def parse_time(value: str) -> time:
    """Parse a wall-clock time such as "09:00". Raises ValueError on anything else."""
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError as err:
        raise ValueError(f"Cannot read '{value}' as a time. Use HH:MM.") from err


def _as_datetime(value: datetime | date) -> datetime:
    """Normalise an icalendar DTSTART/DTEND to a naive datetime.

    All-day events decode to a date; midnight is the only sensible ordering position for
    them. Timezone-aware values are stripped to naive local, since this module compares
    naive datetimes throughout and mixing the two raises at comparison time.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    return datetime.combine(value, time.min)


async def _read_calendar(path: Path) -> Calendar:
    """Read the .ics file, or raise FileNotFoundError naming the path."""
    if not path.is_file():
        raise FileNotFoundError(f"No calendar file at '{path}'")
    async with aiofiles.open(path, 'rb') as f:
        content = await f.read()
    return Calendar.from_ical(content)


def _empty_calendar() -> Calendar:
    """Build an empty VCALENDAR with the properties every reader requires."""
    cal = Calendar()
    cal.add("prodid", _PRODID)
    cal.add("version", "2.0")
    return cal


async def _write_calendar(path: Path, cal: Calendar) -> None:
    """Write the calendar, creating the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, 'wb') as f:
        await f.write(cal.to_ical())


def _event_bounds(event: Event) -> tuple[datetime, datetime]:
    """Return one event's (start, end), defaulting a missing end to one hour."""
    start = _as_datetime(event.decoded("DTSTART"))
    if "DTEND" in event:
        return start, _as_datetime(event.decoded("DTEND"))
    return start, start + timedelta(hours=1)


def _format_event(event: Event) -> str:
    """Render one event as a single line, ending with the id needed to change it."""
    start, end = _event_bounds(event)
    summary = str(event.get("SUMMARY", "(untitled)"))
    uid = str(event.get("UID", ""))
    same_day = start.date() == end.date()
    when = (
        f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
        if same_day
        else f"{start:%Y-%m-%d %H:%M} - {end:%Y-%m-%d %H:%M}"
    )
    line = f"{when}  {summary}"
    location = str(event.get("LOCATION", ""))
    if location:
        line += f"  @ {location}"
    return f"{line}  [id: {uid[:8]}]"


def _sorted_events(cal: Calendar) -> list[Event]:
    """Every VEVENT in the calendar, ordered by start time."""
    return sorted(cal.walk("VEVENT"), key=lambda e: _event_bounds(e)[0])


def _find_event(cal: Calendar, event_id: str) -> Event:
    """Find one event by full UID or by the short id shown in listings.

    A prefix is accepted because listings show the first 8 characters, and asking the model
    to reproduce a full UUID it never saw would guarantee failure. An ambiguous prefix
    raises rather than picking one, since editing the wrong appointment is worse than an
    error message.
    """
    wanted = event_id.strip()
    matches = [e for e in cal.walk("VEVENT") if str(e.get("UID", "")).startswith(wanted)]
    if not matches:
        raise ValueError(f"No event with id '{event_id}'. Use list_events to see current ids.")
    if len(matches) > 1:
        raise ValueError(f"Id '{event_id}' matches {len(matches)} events. Use the full id.")
    return matches[0]


def _overlapping(cal: Calendar, start: datetime, end: datetime, skip_uid: str = "") -> list[Event]:
    """Events that overlap [start, end), excluding one UID. Touching edges do not overlap."""
    hits = []
    for event in _sorted_events(cal):
        if skip_uid and str(event.get("UID", "")) == skip_uid:
            continue
        e_start, e_end = _event_bounds(event)
        if e_start < end and start < e_end:
            hits.append(event)
    return hits


def _set(event: Event, key: str, value: object) -> None:
    """Replace a property. add() appends a second copy, which most readers then ignore."""
    event.pop(key.upper(), None)
    event.add(key, value)


# Tool implementations (async)


async def current_datetime() -> str:
    """Report the current date, time, and weekday."""
    now = datetime.now()
    return f"{now:%Y-%m-%d %H:%M} ({now:%A})"


async def list_events(start: str | None = None, end: str | None = None) -> str:
    """List events, optionally limited to a date range."""
    path = get_calendar_path()
    try:
        cal = await _read_calendar(path)
    except FileNotFoundError:
        return f"No calendar file at '{path}'"
    
    events = _sorted_events(cal)

    window_start = parse_datetime(start) if start else None
    window_end = parse_datetime(end) if end else None
    # A bare end date means "through that whole day", not "up to its midnight".
    if window_end is not None and window_end.time() == time.min:
        window_end += timedelta(days=1)

    if window_start or window_end:
        events = [
            e
            for e in events
            if (window_end is None or _event_bounds(e)[0] < window_end)
            and (window_start is None or _event_bounds(e)[1] > window_start)
        ]

    if not events:
        return "No events found." if not (start or end) else "No events in that range."
    return "\n".join(_format_event(e) for e in events)


async def find_free_slots(
    day: str,
    duration_minutes: int = 60,
    day_start: str = DEFAULT_DAY_START,
    day_end: str = DEFAULT_DAY_END,
) -> str:
    """Report the gaps on one day that are long enough for a given duration."""
    if duration_minutes <= 0:
        return "Error: duration_minutes must be positive."

    try:
        target = parse_datetime(day).date()
        window_start = datetime.combine(target, parse_time(day_start))
        window_end = datetime.combine(target, parse_time(day_end))
    except ValueError as e:
        return f"Error: {e}"
    
    if window_end <= window_start:
        return "Error: day_end must be later than day_start."

    path = get_calendar_path()
    try:
        cal = await _read_calendar(path)
    except FileNotFoundError:
        # No calendar = entire day is free
        return f"Free on {target:%Y-%m-%d} ({duration_minutes} min or longer): {day_start}-{day_end}"

    busy = sorted(
        (
            (max(s, window_start), min(e, window_end))
            for s, e in (_event_bounds(ev) for ev in _sorted_events(cal))
            if s < window_end and e > window_start
        )
    )

    needed = timedelta(minutes=duration_minutes)
    free: list[str] = []
    cursor = window_start
    for busy_start, busy_end in busy:
        if busy_start - cursor >= needed:
            free.append(f"{cursor:%H:%M}-{busy_start:%H:%M}")
        cursor = max(cursor, busy_end)
    if window_end - cursor >= needed:
        free.append(f"{cursor:%H:%M}-{window_end:%H:%M}")

    if not free:
        return (
            f"No free slot of {duration_minutes} min on {target:%Y-%m-%d} "
            f"between {day_start} and {day_end}."
        )
    return f"Free on {target:%Y-%m-%d} ({duration_minutes} min or longer): " + ", ".join(free)


async def create_event(
    title: str,
    start: str,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    """Add an event to the calendar, reporting any clash it creates."""
    if not title.strip():
        return "Error: title must not be empty."

    try:
        starts_at = parse_datetime(start)
        ends_at = parse_datetime(end) if end else starts_at + timedelta(hours=1)
    except ValueError as e:
        return f"Error: {e}"
    
    if ends_at <= starts_at:
        return "Error: end must be later than start."

    path = get_calendar_path()
    if path.is_file():
        cal = await _read_calendar(path)
    else:
        cal = _empty_calendar()

    event = Event()
    uid = uuid.uuid4().hex
    event.add("uid", uid)
    event.add("dtstamp", datetime.now())
    event.add("summary", title.strip())
    event.add("dtstart", starts_at)
    event.add("dtend", ends_at)
    if location:
        event.add("location", location.strip())
    if description:
        event.add("description", description.strip())

    clashes = _overlapping(cal, starts_at, ends_at)
    cal.add_component(event)
    await _write_calendar(path, cal)

    created = f"Created '{title.strip()}' {starts_at:%Y-%m-%d %H:%M}-{ends_at:%H:%M} [id: {uid[:8]}]"
    if clashes:
        # Reported, not refused: the user may well want the double booking, and this
        # tool is not the place to decide that for them.
        names = ", ".join(str(c.get("SUMMARY", "(untitled)")) for c in clashes)
        return f"{created}. Warning: overlaps {len(clashes)} existing event(s): {names}"
    return created


async def update_event(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    """Change one or more fields of an existing event."""
    if all(field is None for field in (title, start, end, location, description)):
        return "Error: Nothing to update: give at least one field to change."

    path = get_calendar_path()
    try:
        cal = await _read_calendar(path)
    except FileNotFoundError:
        return f"Error: No calendar file at '{path}'"
    
    try:
        event = _find_event(cal, event_id)
    except ValueError as e:
        return f"Error: {e}"
    
    old_start, old_end = _event_bounds(event)

    try:
        new_start = parse_datetime(start) if start else old_start
        new_end = parse_datetime(end) if end else old_end
    except ValueError as e:
        return f"Error: {e}"
    
    # Moving only the start would otherwise silently stretch or invert the event.
    if start and not end:
        new_end = new_start + (old_end - old_start)
    if new_end <= new_start:
        return "Error: end must be later than start."

    if title is not None:
        if not title.strip():
            return "Error: title must not be empty."
        _set(event, "summary", title.strip())
    if start or end:
        _set(event, "dtstart", new_start)
        _set(event, "dtend", new_end)
    if location is not None:
        _set(event, "location", location.strip())
    if description is not None:
        _set(event, "description", description.strip())
    _set(event, "dtstamp", datetime.now())

    clashes = _overlapping(cal, new_start, new_end, skip_uid=str(event.get("UID", "")))
    await _write_calendar(path, cal)

    updated = f"Updated {_format_event(event)}"
    if clashes:
        names = ", ".join(str(c.get("SUMMARY", "(untitled)")) for c in clashes)
        return f"{updated}. Warning: overlaps {len(clashes)} existing event(s): {names}"
    return updated
