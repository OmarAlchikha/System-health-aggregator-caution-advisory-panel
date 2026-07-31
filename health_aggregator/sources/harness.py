"""Simulated wiring-harness fault-injection tester / continuity BIT.

STAND-IN for a real harness tester project. Models a scanner that walks a
12-wire harness at 2 Hz measuring, per wire:

* loop resistance (mΩ) — open / high-resistance detection
* insulation resistance to ground and to +28 V (MΩ) — short detection

Fault classification thresholds are generic bench values, not any real
programme's limits. Intermittents are detected at the source (≥3 dropouts
inside a 10 s window) and latched here, because an intermittent that isn't
faulty at the instant you look is exactly the one you must not lose.
"""

from __future__ import annotations

import random
import time

from .base import SimulatedSource

WIRES = {f"W{n:02d}": {"from": f"P1-{n}", "to": f"J4-{n}"} for n in range(1, 13)}

R_LOOP_NOM_MOHM = (20.0, 80.0)     # healthy loop resistance band
R_HIGH_RES_MOHM = 500.0            # above this = high-resistance joint
R_OPEN_MOHM = 1e6                  # effectively open
INS_NOM_MOHM = 200.0               # healthy insulation, MΩ
INS_SHORT_MOHM = 0.1               # below this = short
INTERMITTENT_WINDOW_S = 10.0
INTERMITTENT_COUNT = 3


class HarnessSim(SimulatedSource):
    SOURCE_ID = "harness"
    RATE_HZ = 2.0  # one full harness scan per frame

    def __init__(self, out):
        super().__init__(out)
        self._faults: dict[str, str] = {}          # wire -> injected fault
        self._flicker: dict[str, float] = {}       # wire -> flicker until (mono s)
        self._dropouts: dict[str, list[float]] = {w: [] for w in WIRES}
        self._intermittent: set[str] = set()       # latched at source

    def inject(self, scenario: str, channel: str | None = None) -> bool:
        wire = channel or random.choice(list(WIRES))
        with self._lock:
            if scenario in ("harness_open", "harness_short_gnd",
                            "harness_short_pwr", "harness_high_res"):
                self._faults[wire] = scenario
            elif scenario == "harness_intermittent":
                # transient dropouts, not a steady fault
                self._flicker[wire] = time.monotonic() + 8.0
            elif scenario == "harness_repair":
                self._faults.pop(wire, None)
            else:
                return False
        return True

    def reset(self) -> None:
        with self._lock:
            self._faults.clear()
            self._flicker.clear()
            self._intermittent.clear()
            for lst in self._dropouts.values():
                lst.clear()

    def step(self, dt: float):
        now = time.monotonic()
        frames = {}
        for wire, pins in WIRES.items():
            fault = self._faults.get(wire)
            r_loop = random.uniform(*R_LOOP_NOM_MOHM)
            ins_gnd = INS_NOM_MOHM * random.uniform(0.8, 1.2)
            ins_pwr = INS_NOM_MOHM * random.uniform(0.8, 1.2)

            if fault == "harness_open":
                r_loop = R_OPEN_MOHM
            elif fault == "harness_high_res":
                r_loop = random.uniform(600.0, 900.0)
            elif fault == "harness_short_gnd":
                ins_gnd = random.uniform(0.001, 0.05)
            elif fault == "harness_short_pwr":
                ins_pwr = random.uniform(0.001, 0.05)

            # Intermittent: brief opens that come and go between scans
            if self._flicker.get(wire, 0.0) > now and random.random() < 0.5:
                r_loop = R_OPEN_MOHM
                self._dropouts[wire].append(now)
            self._dropouts[wire] = [
                t for t in self._dropouts[wire] if now - t <= INTERMITTENT_WINDOW_S
            ]
            if len(self._dropouts[wire]) >= INTERMITTENT_COUNT:
                self._intermittent.add(wire)

            conditions = []
            if r_loop >= R_OPEN_MOHM:
                conditions.append("HARNESS_OPEN")
            elif r_loop > R_HIGH_RES_MOHM:
                conditions.append("HARNESS_HIGH_RES")
            if ins_gnd < INS_SHORT_MOHM:
                conditions.append("HARNESS_SHORT_GND")
            if ins_pwr < INS_SHORT_MOHM:
                conditions.append("HARNESS_SHORT_PWR")
            if wire in self._intermittent:
                conditions.append("HARNESS_INTERMITTENT")

            frames[wire] = (conditions, {
                "from": pins["from"], "to": pins["to"],
                "r_loop_mohm": None if r_loop >= R_OPEN_MOHM else round(r_loop, 1),
                "ins_gnd_mohm": round(ins_gnd, 3),
                "ins_pwr_mohm": round(ins_pwr, 3),
                "ok": not conditions,
            })
        return frames
