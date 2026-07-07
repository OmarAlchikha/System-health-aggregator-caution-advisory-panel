#!/usr/bin/env python3
"""Entry point: python run.py [--port 8000]"""

import argparse
from pathlib import Path

from health_aggregator.server import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="System health aggregator panel")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--history", default="logs/fault_history.jsonl",
                    help="JSONL fault-history log path ('' to disable)")
    args = ap.parse_args()
    main(args.host, args.port,
         Path(args.history) if args.history else None)
