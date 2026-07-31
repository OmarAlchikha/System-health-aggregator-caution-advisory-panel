"""System health aggregator: a simplified aircraft caution/advisory panel.

Ingests fault/status envelopes from electrical-system modules (SSPC,
wiring-harness fault tester, automatic bus transfer controller) and
annunciates them by severity, aircraft-CAS style.

All sources in this repository are SIMULATED stand-ins; see README.md.
"""

__version__ = "1.0.0"
