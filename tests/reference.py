"""Betaflight's own decoder's output for tests/data/flight.bbl: github.com/betaflight/blackbox-tools d89f96b
(2026-10-25), `blackbox_decode --unit-flags raw --unit-amperage raw --unit-vbat raw flight.bbl`, gzipped.

It reads no FLIGHT_LOG_EVENT_FLIGHTMODE or _DISARM payloads (parser.c), and so skips the frames up to the next
I-frame after each of those events (test_bbl.py shows which)."""

import csv
import gzip
from pathlib import Path

import numpy as np

LOG = Path(__file__).parent / "data" / "flight.bbl"
DECODED = LOG.parent / "flight.blackbox_decode.csv.gz"


def decoded() -> dict[str, np.ndarray]:
    """Columns by field name, without the unit suffix ("time (us)" -> "time")."""
    with gzip.open(DECODED, "rt") as f:
        names, *rows = csv.reader(f)
    columns = np.array(rows, dtype=float).T
    return {name.strip().split(" (")[0]: column for name, column in zip(names, columns, strict=True)}
