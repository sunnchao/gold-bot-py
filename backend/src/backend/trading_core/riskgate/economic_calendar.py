"""Local UTC calendar and precedence rules for high-impact US releases."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

EVENT_UPCOMING = "event.upcoming_24h"
EVENT_REDUCED = "event.reduced_window"
EVENT_BLACKOUT = "event.blackout_window"
EVENT_LOT_SCALE = 0.5
EVENT_TYPES = {"CPI", "NFP", "CORE_PCE", "FOMC", "FOMC_MINUTES"}


@dataclass(frozen=True)
class EconomicEvent:
    kind: str
    time: datetime
    symbols: frozenset[str]

    @property
    def blackout_start(self) -> datetime:
        return self.time - timedelta(minutes=30)

    @property
    def blackout_end(self) -> datetime:
        return self.time + timedelta(minutes=60 if self.kind == "FOMC" else 30)


@dataclass(frozen=True)
class EventWindow:
    stage: Literal["warning", "reduced", "blackout"]
    event: EconomicEvent

    @property
    def code(self) -> str:
        return {"warning": EVENT_UPCOMING, "reduced": EVENT_REDUCED, "blackout": EVENT_BLACKOUT}[self.stage]

    def context(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "type": self.event.kind,
            "time_utc": self.event.time.isoformat(),
            "blackout_start": self.event.blackout_start.isoformat(),
            "blackout_end": self.event.blackout_end.isoformat(),
        }


@lru_cache(maxsize=4)
def _load_calendar(path: Path) -> tuple[EconomicEvent, ...]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("expected an array of events")
        events = []
        for row in rows:
            kind, time, symbols = row["type"], datetime.fromisoformat(row["time_utc"]), row["symbols"]
            if kind not in EVENT_TYPES or time.utcoffset() != timedelta(0):
                raise ValueError("event type must be supported and time_utc must be UTC")
            if not isinstance(symbols, list) or not symbols or any(
                not isinstance(symbol, str) or not symbol.strip() for symbol in symbols
            ):
                raise ValueError("symbols must be a non-empty array of symbols")
            events.append(EconomicEvent(kind, time, frozenset(symbol.strip().upper() for symbol in symbols)))
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid economic calendar {path}: {error}") from error
    return tuple(sorted(events, key=lambda event: event.time))


def load_economic_calendar() -> tuple[EconomicEvent, ...]:
    override = os.getenv("GB_ECONOMIC_CALENDAR_PATH")
    return _load_calendar(Path(override) if override else Path(__file__).with_name("economic_calendar.json"))


def event_window_for(symbol: str | None, now: datetime) -> EventWindow | None:
    window: EventWindow | None = None
    for event in load_economic_calendar():
        if symbol is not None and symbol not in event.symbols:
            continue
        if event.blackout_start <= now < event.blackout_end:
            return EventWindow("blackout", event)
        if event.time - timedelta(hours=4) <= now < event.blackout_start:
            if window is None or window.stage == "warning":
                window = EventWindow("reduced", event)
        elif event.time - timedelta(hours=24) <= now < event.time and window is None:
            window = EventWindow("warning", event)
    return window
