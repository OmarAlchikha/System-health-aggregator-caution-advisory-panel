"""Caution/Advisory System core: severity policy, debounce, latching, history.

The aggregator is deliberately dumb about physics and smart about
annunciation. It never re-derives whether an SSPC should have tripped; it
only decides whether a reported condition is *worth telling the crew about*,
when, and at what priority.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .envelope import Envelope, Severity, wall_iso

# Debounce on the clear side: a condition must stay gone this long before
# its message is removed, so a chattering fault doesn't strobe the panel.
DEFAULT_CLEAR_MS = 500.0


@dataclass(frozen=True)
class Rule:
    """Annunciation policy for one (source, condition) pair."""

    text: str                       # message template; {channel} substituted
    severity: Severity
    debounce_ms: float = 0.0        # condition must persist this long to annunciate
    latch: bool = False             # once annunciated, stays until reset even if cleared
    clear_ms: float = DEFAULT_CLEAR_MS
    # Optional escalation: return a different severity based on telemetry
    # (e.g. an instant trip on an essential channel is a WARNING, otherwise
    # a CAUTION). Policy stays here, not in the source.
    severity_fn: Callable[[dict], Severity] | None = None

    def resolve_severity(self, value: dict) -> Severity:
        return self.severity_fn(value) if self.severity_fn else self.severity


def _essential_escalation(warn_if: Severity, otherwise: Severity):
    def fn(value: dict) -> Severity:
        return warn_if if value.get("essential") else otherwise
    return fn


# The single severity policy table. Timing rationale is in the README.
RULES: dict[tuple[str, str], Rule] = {
    # --- SSPC (solid-state power controller, I²t trip logic) ---
    ("sspc", "SSPC_TRIP_INSTANT"): Rule(
        "ELEC SSPC {channel} TRIP (INST)",
        Severity.CAUTION, debounce_ms=0, latch=True,
        severity_fn=_essential_escalation(Severity.WARNING, Severity.CAUTION),
    ),
    ("sspc", "SSPC_TRIP_I2T"): Rule(
        "ELEC SSPC {channel} TRIP (I2T)",
        Severity.CAUTION, debounce_ms=0, latch=True,
    ),
    ("sspc", "SSPC_OVERLOAD"): Rule(
        # Pre-trip heads-up while the I²t accumulator is charging. 500 ms
        # debounce so motor inrush and switching transients never annunciate.
        "ELEC SSPC {channel} OVERLOAD",
        Severity.ADVISORY, debounce_ms=500,
    ),
    # --- Wiring harness fault-injection tester / continuity BIT ---
    ("harness", "HARNESS_SHORT_PWR"): Rule(
        # Hot short: highest-consequence wiring fault (fire risk), so it
        # outranks every other harness finding and latches.
        "WIRING {channel} SHORT TO PWR",
        Severity.WARNING, debounce_ms=250, latch=True,
    ),
    ("harness", "HARNESS_SHORT_GND"): Rule(
        "WIRING {channel} SHORT TO GND",
        Severity.CAUTION, debounce_ms=500,
    ),
    ("harness", "HARNESS_OPEN"): Rule(
        # Two full scan cycles (scanner runs at 2 Hz) before annunciating,
        # so a single bad probe contact doesn't page anyone.
        "WIRING {channel} OPEN CIRCUIT",
        Severity.CAUTION, debounce_ms=1000,
    ),
    ("harness", "HARNESS_HIGH_RES"): Rule(
        "WIRING {channel} HIGH RESISTANCE",
        Severity.ADVISORY, debounce_ms=2000,
    ),
    ("harness", "HARNESS_INTERMITTENT"): Rule(
        # Latched: an intermittent that "went away" is exactly the fault you
        # must not lose. The source also latches it; belt and suspenders.
        "WIRING {channel} INTERMITTENT",
        Severity.ADVISORY, debounce_ms=0, latch=True,
    ),
    # --- Automatic bus transfer controller ---
    ("bus_xfr", "BUS_XFER_FAIL"): Rule(
        "ELEC {channel} XFER FAIL",
        Severity.WARNING, debounce_ms=0, latch=True,
    ),
    ("bus_xfr", "BUS_ON_ALTERNATE"): Rule(
        "ELEC {channel} ON ALTN SRC",
        Severity.CAUTION, debounce_ms=0,
    ),
    ("bus_xfr", "BUS_XFER_IN_PROG"): Rule(
        "ELEC {channel} XFER IN PROG",
        Severity.ADVISORY, debounce_ms=0,
    ),
    ("bus_xfr", "MAIN_UNDERVOLT"): Rule(
        # 50 ms: shorter than the controller's own 100 ms qualification so
        # the advisory appears just before the transfer sequence does.
        "ELEC {channel} UNDERVOLT",
        Severity.ADVISORY, debounce_ms=50,
    ),
}


@dataclass
class _Tracked:
    """Per-condition annunciation state machine."""

    rule: Rule
    source: str
    channel: str
    condition: str
    severity: Severity
    simulated: bool
    first_seen_ms: float
    last_seen_ms: float
    annunciated: bool = False
    annunciated_at_ms: float = 0.0
    annunciated_wall: str = ""
    acked: bool = False
    condition_present: bool = True
    value: dict = field(default_factory=dict)

    def message(self) -> str:
        return self.rule.text.format(channel=self.channel)


class CautionAdvisorySystem:
    """Thread-safe aggregator. Feed it envelopes; poll snapshot() for the UI.

    Takes an injectable monotonic clock (ms) so debounce/latch behaviour is
    unit-testable without real sleeps.
    """

    def __init__(
        self,
        history_path: Path | None = None,
        now_ms: Callable[[], float] | None = None,
        history_limit: int = 1000,
    ):
        self._now = now_ms or (lambda: time.monotonic() * 1000.0)
        self._lock = threading.Lock()
        self._tracked: dict[tuple[str, str, str], _Tracked] = {}
        self._telemetry: dict[str, dict] = {}       # per-source UI tile data
        self._history: list[dict] = []
        self._history_limit = history_limit
        self._history_path = history_path
        if history_path:
            history_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- ingest

    def ingest(self, env: Envelope) -> None:
        now = self._now()
        with self._lock:
            tile = self._telemetry.setdefault(
                env.source, {"simulated": env.simulated, "channels": {}}
            )
            tile["channels"][env.channel] = env.value
            tile["last_frame_wall"] = env.t_wall
            tile["last_frame_ms"] = now

            present = set(env.conditions)
            for cond in present:
                key = (env.source, env.channel, cond)
                rule = RULES.get((env.source, cond))
                if rule is None:
                    continue  # unknown condition: telemetry only, never annunciated
                tr = self._tracked.get(key)
                if tr is None or (not tr.condition_present and not tr.annunciated):
                    self._tracked[key] = _Tracked(
                        rule=rule, source=env.source, channel=env.channel,
                        condition=cond,
                        severity=rule.resolve_severity(env.value),
                        simulated=env.simulated,
                        first_seen_ms=now, last_seen_ms=now, value=env.value,
                    )
                else:
                    if not tr.condition_present:
                        tr.condition_present = True
                        tr.first_seen_ms = now  # restart persistence timer
                    tr.last_seen_ms = now
                    tr.value = env.value
                    # Re-resolve severity: an escalating fault may change class
                    tr.severity = max(tr.severity, rule.resolve_severity(env.value))

            # Conditions this frame no longer reports are "gone" for this channel
            for key, tr in self._tracked.items():
                if key[0] == env.source and key[1] == env.channel and key[2] not in present:
                    tr.condition_present = False

        self.tick()

    # --------------------------------------------------------------- tick

    def tick(self) -> None:
        """Advance debounce/clear timers. Called on ingest and by the server."""
        now = self._now()
        with self._lock:
            dead: list[tuple[str, str, str]] = []
            for key, tr in self._tracked.items():
                if tr.condition_present and not tr.annunciated:
                    if now - tr.first_seen_ms >= tr.rule.debounce_ms:
                        tr.annunciated = True
                        tr.annunciated_at_ms = now
                        tr.annunciated_wall = wall_iso()
                        self._log("ASSERT", tr)
                elif not tr.condition_present:
                    gone_for = now - tr.last_seen_ms
                    if tr.annunciated:
                        if not tr.rule.latch and gone_for >= tr.rule.clear_ms:
                            self._log("CLEAR", tr)
                            dead.append(key)
                    elif gone_for >= tr.rule.clear_ms:
                        dead.append(key)  # never met debounce; drop silently
            for key in dead:
                del self._tracked[key]

    # ------------------------------------------------------------ actions

    def acknowledge(self) -> None:
        """Cancel master WARNING/CAUTION flashers; messages stay displayed."""
        with self._lock:
            for tr in self._tracked.values():
                if tr.annunciated and not tr.acked:
                    tr.acked = True
                    self._log("ACK", tr)

    def reset_latched(self) -> None:
        """Clear latched messages whose underlying condition is gone.

        Deliberately does NOT clear a latched message while the condition is
        still present — you can't reset your way out of an active fault.
        """
        with self._lock:
            dead = []
            for key, tr in self._tracked.items():
                if tr.annunciated and tr.rule.latch and not tr.condition_present:
                    self._log("RESET", tr)
                    dead.append(key)
            for key in dead:
                del self._tracked[key]

    # ------------------------------------------------------------ queries

    def active_messages(self) -> list[dict]:
        """Annunciated messages, most severe first, newest first within a level."""
        with self._lock:
            msgs = [
                {
                    "message": tr.message(),
                    "severity": tr.severity.name,
                    "source": tr.source,
                    "channel": tr.channel,
                    "condition": tr.condition,
                    "asserted_wall": tr.annunciated_wall,
                    "age_s": round((self._now() - tr.annunciated_at_ms) / 1000.0, 1),
                    "acked": tr.acked,
                    "latched": tr.rule.latch,
                    "condition_present": tr.condition_present,
                    "simulated": tr.simulated,
                }
                for tr in self._tracked.values()
                if tr.annunciated
            ]
        msgs.sort(key=lambda m: (-Severity[m["severity"]].value, m["age_s"]))
        return msgs

    def masters(self) -> dict:
        """Master annunciator state: lit if any message at level; flashing if unacked."""
        out = {
            "WARNING": {"lit": False, "flashing": False},
            "CAUTION": {"lit": False, "flashing": False},
        }
        for m in self.active_messages():
            if m["severity"] in out:
                out[m["severity"]]["lit"] = True
                if not m["acked"]:
                    out[m["severity"]]["flashing"] = True
        return out

    def snapshot(self) -> dict:
        self.tick()
        with self._lock:
            telemetry = json.loads(json.dumps(self._telemetry))  # deep copy
        return {
            "t_wall": wall_iso(),
            "messages": self.active_messages(),
            "masters": self.masters(),
            "telemetry": telemetry,
        }

    def history(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return self._history[-limit:][::-1]

    # ------------------------------------------------------------ logging

    def _log(self, event: str, tr: _Tracked) -> None:
        entry = {
            "t_wall": wall_iso(),
            "event": event,             # ASSERT | CLEAR | ACK | RESET
            "severity": tr.severity.name,
            "message": tr.message(),
            "source": tr.source,
            "channel": tr.channel,
            "condition": tr.condition,
            "simulated": tr.simulated,
        }
        self._history.append(entry)
        if len(self._history) > self._history_limit:
            del self._history[: -self._history_limit]
        if self._history_path:
            try:
                with self._history_path.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass  # history file is best-effort; never take down annunciation
