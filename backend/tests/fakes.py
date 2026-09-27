"""
Test doubles.

The suite never reaches the network: the OpenAI client is replaced by a script
of canned model turns, and Google Calendar / the weather providers are swapped
for in-memory stubs. That keeps the tests free, fast and deterministic, and it
lets us assert on exactly what the model was sent.
"""

import contextlib
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# app.py builds an OpenAI client at import time; it is replaced before any call.
os.environ.setdefault("OPENAI_API_KEY", "test-key-never-used")


# --------------------------------------------------------------------------- #
# a scripted model
# --------------------------------------------------------------------------- #

class _Function:
    def __init__(self, name, args):
        self.name = name
        self.arguments = json.dumps(args)


class Call:
    """One tool call in a scripted model turn."""

    _counter = 0

    def __init__(self, name, args=None):
        Call._counter += 1
        self.id = f"call_{Call._counter}"
        self.type = "function"
        self.function = _Function(name, args or {})


class Msg:
    """One scripted model turn: plain text, tool calls, or both."""

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class FakeClient:
    """
    Stands in for the OpenAI client.

    Replays `script` one turn per request and records every request in `seen`,
    so a test can check what the loop actually sent — in particular that tool
    results arrived as messages on the following request.
    """

    def __init__(self, script=None):
        self.script = list(script or [])
        self.seen = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.seen.append(kwargs)
                turn = outer.script.pop(0) if outer.script else Msg("(script exhausted)")
                return _Response(turn)

        self.chat = type("Chat", (), {"completions": _Completions()})()

    def load(self, script):
        """Reset for another request."""
        self.script = list(script)
        self.seen = []

    def last_request_messages(self):
        return self.seen[-1]["messages"]


def tool_messages(messages):
    """The observations appended to a transcript, decoded."""
    return [json.loads(m["content"]) for m in messages if m.get("role") == "tool"]


# --------------------------------------------------------------------------- #
# stubbed calendar and weather
# --------------------------------------------------------------------------- #

# "Now" for every stubbed test: Monday of the week the sample events sit in.
# The code under test works out presets like "this_week" from the current time, so
# leaving the real clock in place made the suite start failing once that week ended.
FROZEN_NOW = "2026-09-21T09:00:00+10:00"

# Two 'Gym' entries on different days, so title matching and event_ids both matter.
SAMPLE_EVENTS = [
    {"id": "e1", "summary": "Gym",
     "start": {"dateTime": "2026-09-23T07:00:00+10:00"},
     "end": {"dateTime": "2026-09-23T08:00:00+10:00"}},
    {"id": "e2", "summary": "Team meeting",
     "start": {"dateTime": "2026-09-23T14:00:00+10:00"},
     "end": {"dateTime": "2026-09-23T15:00:00+10:00"}},
    {"id": "e3", "summary": "Gym",
     "start": {"dateTime": "2026-09-25T07:00:00+10:00"},
     "end": {"dateTime": "2026-09-25T08:00:00+10:00"}},
]

SAMPLE_WEATHER = {
    "place_name": "Sydney, NSW",
    "temp": 19, "feels": 18, "humidity": 70, "wind": 4,
    "cond": "light rain",
    "tips": "Possible rain: carry an umbrella and watch for slippery roads.",
}


class Recorder:
    """Collects the writes a test's stubs received."""

    def __init__(self):
        self.created = []
        self.updated = []
        self.deleted = []


@contextlib.contextmanager
def stubbed_google(events=None, connected=True):
    """Swap google_calendar's functions inside tool_handlers for in-memory ones,
    and pin the clock to FROZEN_NOW so date presets resolve to the sample week."""
    from datetime import datetime

    import app
    import formatting
    import tool_handlers

    events = SAMPLE_EVENTS if events is None else events
    rec = Recorder()
    originals = {
        name: getattr(tool_handlers, name)
        for name in ("is_connected", "find_events", "create_calendar_event",
                     "update_calendar_event", "delete_calendar_event")
    }

    def fake_find(time_min, time_max, max_results=50):
        # An all-day event carries a plain `date` and no `dateTime`, exactly as the
        # Google API returns it; the double has to show that or nothing downstream
        # is ever exercised against it.
        lo, hi = time_min.isoformat(), time_max.isoformat()

        def in_window(e):
            start = e["start"]
            if "dateTime" in start:
                return lo <= start["dateTime"] <= hi
            # All-day: a date and no time, so there is only a date to compare.
            return lo[:10] <= start.get("date", "") <= hi[:10]

        return [e for e in events if in_window(e)][:max_results]

    def fake_create(**kwargs):
        rec.created.append(kwargs)
        return {"id": f"new{len(rec.created)}", "summary": kwargs.get("summary")}

    # app.py imported the clock by name, so it holds its own reference to patch.
    frozen_now = datetime.fromisoformat(FROZEN_NOW)
    clocks = {mod: mod._current_sydney_datetime for mod in (formatting, app)}
    for mod in clocks:
        mod._current_sydney_datetime = lambda: frozen_now

    tool_handlers.is_connected = lambda: connected
    tool_handlers.find_events = fake_find
    tool_handlers.create_calendar_event = fake_create
    tool_handlers.update_calendar_event = lambda eid, **kw: rec.updated.append((eid, kw))
    tool_handlers.delete_calendar_event = lambda eid: rec.deleted.append(eid)
    try:
        yield rec
    finally:
        for name, fn in originals.items():
            setattr(tool_handlers, name, fn)
        for mod, fn in clocks.items():
            mod._current_sydney_datetime = fn


# Two named venues at known distances, one with an address recorded in OSM and one
# without, so tests can check that a missing field is reported rather than invented.
SAMPLE_PLACES = [
    {"name": "Amber Bar", "kind": "bar", "distance_m": 88,
     "address": "122 Pitt Street, Sydney", "opening_hours": "Mo-Sa 16:00-24:00",
     "website": None},
    {"name": "SubSolo", "kind": "bar", "distance_m": 84,
     "address": None, "opening_hours": None, "website": None},
]


@contextlib.contextmanager
def stubbed_places(places=None, error=None):
    """Swap the OpenStreetMap lookup, or make it fail."""
    import tool_handlers

    original = tool_handlers.find_nearby

    def fake(category, lat, lon, radius_m=None, limit=None, keyword=None):
        if error:
            raise error
        if category not in tool_handlers.CATEGORY_TAGS:
            raise ValueError(f"Unknown category '{category}'.")
        return SAMPLE_PLACES if places is None else places

    tool_handlers.find_nearby = fake
    try:
        yield
    finally:
        tool_handlers.find_nearby = original


@contextlib.contextmanager
def stubbed_weather(conditions=SAMPLE_WEATHER):
    """Swap the weather lookup so no provider is contacted."""
    import tool_handlers

    original = tool_handlers.current_conditions
    tool_handlers.current_conditions = lambda lat, lon: conditions
    try:
        yield
    finally:
        tool_handlers.current_conditions = original
