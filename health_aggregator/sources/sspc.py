"""Simulated solid-state power controller (SSPC) with I²t trip logic.

STAND-IN for a real SSPC project. Models the two protection mechanisms a
textbook SSPC provides (public-knowledge behaviour only — no vendor or
aircraft-specific values):

* Thermal (I²t): while current exceeds ~110% of rating, the accumulator
  integrates (I² − Ir²)·dt — the overload heat the wire has to absorb.
  Below that it decays with a thermal time constant, modelling the wire
  cooling back down. Trip when the accumulator reaches a limit sized so a
  200% overload trips in ~5 s (inverse-time curve: bigger overload, faster
  trip — same shape as a thermal breaker but electronically repeatable).
* Instantaneous: current ≥ 8× rating in a single 100 ms sample is treated
  as a bolted short and trips immediately, no integration.

Trips latch at the source (a real SSPC stays open until commanded reset).
"""

from __future__ import annotations

import math
import random

from .base import SimulatedSource

CHANNELS = {
    # name, rated A, essential? (generic textbook loads; "essential" drives
    # severity escalation in the aggregator, not behaviour here)
    "CH1": {"load": "FUEL PUMP A", "rated_a": 10.0, "essential": True},
    "CH2": {"load": "PITOT HEAT", "rated_a": 7.5, "essential": True},
    "CH3": {"load": "CABIN FANS", "rated_a": 15.0, "essential": False},
    "CH4": {"load": "GALLEY", "rated_a": 20.0, "essential": False},
    "CH5": {"load": "NAV LIGHTS", "rated_a": 5.0, "essential": False},
    "CH6": {"load": "UTIL EQUIP", "rated_a": 10.0, "essential": False},
}

OVERLOAD_PU = 1.10      # I²t starts accumulating above 110% of rating
INSTANT_PU = 8.0        # ≥ 8× rating in one sample = instantaneous trip
TAU_COOL_S = 30.0       # thermal memory time constant while cooling
# Trip limit: energy of a 200% overload sustained for 5 s
def _trip_limit(rated: float) -> float:
    return ((2.0 * rated) ** 2 - rated ** 2) * 5.0


class SspcSim(SimulatedSource):
    SOURCE_ID = "sspc"
    RATE_HZ = 10.0

    def __init__(self, out):
        super().__init__(out)
        self._st = {
            ch: {
                "load_pu": random.uniform(0.4, 0.7),  # commanded load, per-unit
                "i2t": 0.0,
                "state": "ON",   # ON | TRIP_I2T | TRIP_INSTANT
            }
            for ch in CHANNELS
        }

    def inject(self, scenario: str, channel: str | None = None) -> bool:
        ch = channel or random.choice(list(CHANNELS))
        with self._lock:
            if scenario == "sspc_overload":
                self._st[ch]["load_pu"] = 1.8
            elif scenario == "sspc_short":
                self._st[ch]["load_pu"] = 12.0
            elif scenario == "sspc_normal":
                self._st[ch]["load_pu"] = random.uniform(0.4, 0.7)
            else:
                return False
        return True

    def reset(self) -> None:
        with self._lock:
            for st in self._st.values():
                if st["state"] != "ON":
                    st["state"] = "ON"
                    st["i2t"] = 0.0
                    st["load_pu"] = random.uniform(0.4, 0.7)

    def step(self, dt: float):
        frames = {}
        for ch, cfg in CHANNELS.items():
            st = self._st[ch]
            rated = cfg["rated_a"]

            if st["state"] == "ON":
                i = st["load_pu"] * rated * random.uniform(0.97, 1.03)
            else:
                i = 0.0  # tripped: contactor open, no current

            conditions = []
            if st["state"] == "ON":
                if i >= INSTANT_PU * rated:
                    st["state"] = "TRIP_INSTANT"
                    i = 0.0
                elif i > OVERLOAD_PU * rated:
                    st["i2t"] += (i * i - rated * rated) * dt
                    conditions.append("SSPC_OVERLOAD")
                    if st["i2t"] >= _trip_limit(rated):
                        st["state"] = "TRIP_I2T"
                        i = 0.0
                else:
                    st["i2t"] *= math.exp(-dt / TAU_COOL_S)

            if st["state"] == "TRIP_I2T":
                conditions = ["SSPC_TRIP_I2T"]
            elif st["state"] == "TRIP_INSTANT":
                conditions = ["SSPC_TRIP_INSTANT"]

            frames[ch] = (conditions, {
                "load": cfg["load"],
                "essential": cfg["essential"],
                "rated_a": rated,
                "current_a": round(i, 2),
                "i2t_pct": round(100.0 * st["i2t"] / _trip_limit(rated), 1),
                "state": st["state"],
            })
        return frames
