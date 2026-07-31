"""Unit tests for the annunciation core, driven by a fake clock.

The CAS takes an injectable now_ms() precisely so these tests need no
real sleeps — debounce and clear timing are exercised deterministically.
"""

import unittest

from health_aggregator.cas import CautionAdvisorySystem
from health_aggregator.envelope import Envelope


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms


def env(source, channel, conditions, value=None, seq=1):
    return Envelope(source=source, channel=channel, conditions=conditions,
                    value=value or {}, simulated=True, seq=seq)


class CasTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cas = CautionAdvisorySystem(now_ms=self.clock)

    def msgs(self):
        return self.cas.active_messages()

    # --- debounce -------------------------------------------------------

    def test_debounced_condition_not_annunciated_immediately(self):
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))
        self.assertEqual(self.msgs(), [])  # 500 ms debounce not yet met

    def test_debounced_condition_annunciates_after_persistence(self):
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))
        self.clock.advance(600)
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))
        m = self.msgs()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["severity"], "ADVISORY")
        self.assertIn("CH1", m[0]["message"])

    def test_transient_shorter_than_debounce_never_annunciates(self):
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))
        self.clock.advance(200)
        self.cas.ingest(env("sspc", "CH1", []))       # gone before 500 ms
        self.clock.advance(600)
        self.cas.tick()
        self.assertEqual(self.msgs(), [])

    def test_debounce_timer_restarts_after_dropout(self):
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))
        self.clock.advance(400)
        self.cas.ingest(env("sspc", "CH1", []))       # dropout at 400 ms
        self.clock.advance(100)
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))  # back again
        self.clock.advance(400)
        self.cas.ingest(env("sspc", "CH1", ["SSPC_OVERLOAD"]))
        # only 400 ms of continuous presence since restart: still silent
        self.assertEqual(self.msgs(), [])

    # --- zero-debounce + latch -----------------------------------------

    def test_trip_annunciates_immediately_and_latches(self):
        self.cas.ingest(env("sspc", "CH2", ["SSPC_TRIP_I2T"]))
        self.assertEqual(len(self.msgs()), 1)
        # condition clears (e.g. source rebooted) but message must persist
        self.cas.ingest(env("sspc", "CH2", []))
        self.clock.advance(10_000)
        self.cas.tick()
        m = self.msgs()
        self.assertEqual(len(m), 1)
        self.assertTrue(m[0]["latched"])
        self.assertFalse(m[0]["condition_present"])

    def test_reset_clears_latched_only_when_condition_gone(self):
        self.cas.ingest(env("bus_xfr", "ESS_BUS", ["BUS_XFER_FAIL"]))
        self.cas.reset_latched()                       # condition still present
        self.assertEqual(len(self.msgs()), 1, "cannot reset an active fault")
        self.cas.ingest(env("bus_xfr", "ESS_BUS", []))
        self.cas.reset_latched()
        self.assertEqual(self.msgs(), [])

    # --- clear debounce (anti-chatter) ----------------------------------

    def test_nonlatched_message_survives_brief_dropout(self):
        self.cas.ingest(env("bus_xfr", "ESS_BUS", ["BUS_ON_ALTERNATE"]))
        self.assertEqual(len(self.msgs()), 1)
        self.cas.ingest(env("bus_xfr", "ESS_BUS", []))
        self.clock.advance(200)                        # < 500 ms clear window
        self.cas.tick()
        self.assertEqual(len(self.msgs()), 1)
        self.clock.advance(400)                        # now past the window
        self.cas.tick()
        self.assertEqual(self.msgs(), [])

    # --- severity policy -------------------------------------------------

    def test_instant_trip_escalates_on_essential_channel(self):
        self.cas.ingest(env("sspc", "CH1", ["SSPC_TRIP_INSTANT"],
                            {"essential": True}))
        self.cas.ingest(env("sspc", "CH5", ["SSPC_TRIP_INSTANT"],
                            {"essential": False}))
        by_ch = {m["channel"]: m["severity"] for m in self.msgs()}
        self.assertEqual(by_ch["CH1"], "WARNING")
        self.assertEqual(by_ch["CH5"], "CAUTION")

    def test_sorted_by_severity_then_age(self):
        self.cas.ingest(env("harness", "W01", ["HARNESS_INTERMITTENT"]))  # advisory
        self.clock.advance(50)
        self.cas.ingest(env("bus_xfr", "ESS_BUS", ["BUS_XFER_FAIL"]))     # warning
        self.clock.advance(50)
        self.cas.ingest(env("sspc", "CH3", ["SSPC_TRIP_I2T"]))            # caution
        sev = [m["severity"] for m in self.msgs()]
        self.assertEqual(sev, ["WARNING", "CAUTION", "ADVISORY"])

    # --- masters & acknowledge ------------------------------------------

    def test_master_flashes_until_acked_then_stays_lit(self):
        self.cas.ingest(env("bus_xfr", "ESS_BUS", ["BUS_XFER_FAIL"]))
        m = self.cas.masters()["WARNING"]
        self.assertTrue(m["lit"] and m["flashing"])
        self.cas.acknowledge()
        m = self.cas.masters()["WARNING"]
        self.assertTrue(m["lit"])
        self.assertFalse(m["flashing"])

    # --- history ----------------------------------------------------------

    def test_history_records_assert_ack_and_reset(self):
        self.cas.ingest(env("bus_xfr", "ESS_BUS", ["BUS_XFER_FAIL"]))
        self.cas.acknowledge()
        self.cas.ingest(env("bus_xfr", "ESS_BUS", []))
        self.cas.reset_latched()
        events = [h["event"] for h in self.cas.history()]
        self.assertEqual(events, ["RESET", "ACK", "ASSERT"])  # newest first

    def test_unknown_condition_is_ignored(self):
        self.cas.ingest(env("sspc", "CH1", ["NOT_A_REAL_CONDITION"]))
        self.clock.advance(5000)
        self.cas.tick()
        self.assertEqual(self.msgs(), [])


if __name__ == "__main__":
    unittest.main()
