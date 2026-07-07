"""Simulated automatic bus transfer (ABT) controller.

STAND-IN for a real bus transfer project. One essential DC bus fed from a
MAIN source (28 V generator) with automatic break-before-make transfer to an
ALTERNATE source (battery bus) on main undervoltage. Generic 28 VDC
architecture from public literature — no aircraft-specific bus names.

Timing model (the part the aggregator's debounce has to respect):
* undervolt qualification: V < 22 V continuously for 100 ms before the
  controller commits — rides through switching transients;
* dead-transfer time: ~150 ms with the bus unpowered (break-before-make so
  two sources are never paralleled);
* if the alternate is also unavailable, the controller declares XFER FAIL
  and latches it.

Runs at 20 Hz because its dynamics (100–150 ms) are the fastest of the
three sources; 10 Hz sampling would alias the transfer sequence.
"""

from __future__ import annotations

import random

from .base import SimulatedSource

V_NOM = 28.0
V_ALT = 26.8            # battery bus slightly below generator
V_UV_THRESH = 22.0
UV_QUAL_S = 0.100       # undervolt qualification time
XFER_DEAD_S = 0.150     # break-before-make dead time


class BusTransferSim(SimulatedSource):
    SOURCE_ID = "bus_xfr"
    RATE_HZ = 20.0

    def __init__(self, out):
        super().__init__(out)
        self._state = "ON_MAIN"     # ON_MAIN | XFER_IN_PROG | ON_ALT | XFER_FAIL
        self._main_ok = True
        self._alt_ok = True
        self._uv_timer = 0.0
        self._xfer_timer = 0.0

    def inject(self, scenario: str, channel: str | None = None) -> bool:
        with self._lock:
            if scenario == "bus_main_fail":
                self._main_ok = False
            elif scenario == "bus_xfer_fail":
                self._main_ok = False
                self._alt_ok = False
            elif scenario == "bus_restore":
                self._main_ok = True
                self._alt_ok = True
                if self._state != "XFER_FAIL":  # fail state needs explicit reset
                    self._state = "ON_MAIN"
                self._uv_timer = 0.0
            else:
                return False
        return True

    def reset(self) -> None:
        with self._lock:
            self._main_ok = True
            self._alt_ok = True
            self._state = "ON_MAIN"
            self._uv_timer = self._xfer_timer = 0.0

    def step(self, dt: float):
        v_main = V_NOM * random.uniform(0.995, 1.005) if self._main_ok \
            else random.uniform(2.0, 15.0)
        v_alt = V_ALT * random.uniform(0.995, 1.005) if self._alt_ok \
            else random.uniform(0.0, 5.0)

        conditions = []
        if self._state == "ON_MAIN":
            if v_main < V_UV_THRESH:
                self._uv_timer += dt
                conditions.append("MAIN_UNDERVOLT")
                if self._uv_timer >= UV_QUAL_S:
                    self._state = "XFER_IN_PROG"
                    self._xfer_timer = 0.0
            else:
                self._uv_timer = 0.0
        elif self._state == "XFER_IN_PROG":
            conditions.append("BUS_XFER_IN_PROG")
            self._xfer_timer += dt
            if self._xfer_timer >= XFER_DEAD_S:
                if v_alt >= V_UV_THRESH:
                    self._state = "ON_ALT"
                else:
                    self._state = "XFER_FAIL"
        elif self._state == "ON_ALT":
            conditions.append("BUS_ON_ALTERNATE")
            if v_main >= V_UV_THRESH:
                # main restored: transfer back after the same qualification
                self._uv_timer += dt
                if self._uv_timer >= UV_QUAL_S:
                    self._state = "ON_MAIN"
                    self._uv_timer = 0.0
            else:
                self._uv_timer = 0.0
        elif self._state == "XFER_FAIL":
            conditions.append("BUS_XFER_FAIL")   # latched until reset

        if self._state == "XFER_IN_PROG":
            v_bus = 0.0                     # dead time: bus unpowered
        elif self._state == "ON_ALT":
            v_bus = v_alt
        elif self._state == "XFER_FAIL":
            v_bus = 0.0
        else:
            v_bus = v_main

        return {"ESS_BUS": (conditions, {
            "state": self._state,
            "v_main": round(v_main, 2),
            "v_alt": round(v_alt, 2),
            "v_bus": round(v_bus, 2),
        })}
