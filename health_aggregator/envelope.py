"""Common signal envelope shared by every source module.

Design rule: sources report *facts* (measurements and raw condition flags);
they never assign severity. Severity is annunciation policy and lives in one
place, the aggregator (cas.py), the same way a CAS/EICAS owns message
priority rather than each LRU deciding how alarming it is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum


class Severity(IntEnum):
    """Annunciation levels, ordered so higher value = more severe.

    Follows the public convention in FAA AC 25.1322-1: red warning,
    amber caution, and an advisory level for awareness-only items.
    """

    ADVISORY = 1
    CAUTION = 2
    WARNING = 3


def wall_iso(t: float | None = None) -> str:
    dt = datetime.fromtimestamp(t if t is not None else time.time(), tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds")


@dataclass
class Envelope:
    """One status frame from one channel of one source.

    Level-based, not edge-based: every frame carries the complete set of
    conditions currently true for the channel, so a lost frame degrades to
    latency instead of a lost fault (see README, "level vs edge").
    """

    source: str                 # "sspc" | "harness" | "bus_xfr"
    channel: str                # e.g. "CH3", "W07", "MAIN_BUS"
    conditions: list[str]       # raw condition flags, e.g. ["SSPC_TRIP_I2T"]
    value: dict                 # telemetry for the UI tile (currents, ohms, volts)
    simulated: bool             # True => stand-in data, flagged end to end
    seq: int
    t_mono_ms: float = field(default_factory=lambda: time.monotonic() * 1000.0)
    t_wall: str = field(default_factory=wall_iso)
