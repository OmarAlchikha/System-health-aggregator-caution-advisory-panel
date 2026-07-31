"""Base class for simulated stand-in sources.

NOTE — STAND-IN DATA: none of the three upstream portfolio projects (SSPC,
harness fault-injection tester, automatic bus transfer controller) are
present in this repository, so every source here is a behavioural simulation
of what such a module would report. Each envelope is tagged simulated=True
and the UI shows a SIM tag on every tile and message. Swapping in a real
module means implementing step() against real hardware/serial input and
setting SIMULATED = False; the aggregator does not change.
"""

from __future__ import annotations

import queue
import threading
import time

from ..envelope import Envelope


class SimulatedSource(threading.Thread):
    SIMULATED = True
    SOURCE_ID = "base"
    RATE_HZ = 10.0

    def __init__(self, out: "queue.Queue[Envelope]"):
        super().__init__(daemon=True, name=f"src-{self.SOURCE_ID}")
        self.out = out
        self._seq = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()  # guards scenario state vs. step()

    # Subclasses implement: one simulation step of dt seconds, returning
    # {channel: (conditions, value)} for every channel, every step
    # (level-based reporting; see envelope.py).
    def step(self, dt: float) -> dict[str, tuple[list[str], dict]]:
        raise NotImplementedError

    def inject(self, scenario: str, channel: str | None = None) -> bool:
        """Apply a named demo scenario. Returns False if unknown."""
        return False

    def reset(self) -> None:
        """Clear injected faults / latched trips (maintenance reset)."""

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        period = 1.0 / self.RATE_HZ
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            dt, last = now - last, now
            with self._lock:
                frames = self.step(dt)
            for channel, (conditions, value) in frames.items():
                self._seq += 1
                self.out.put(Envelope(
                    source=self.SOURCE_ID, channel=channel,
                    conditions=conditions, value=value,
                    simulated=self.SIMULATED, seq=self._seq,
                ))
            time.sleep(period)
