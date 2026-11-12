import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from fakes import flight
from fpv_tools.firmware import Version, version_from_msp

# A clone of github.com/betaflight/betaflight with the maintenance branches, e.g. BETAFLIGHT_SOURCE=~/src/betaflight
SOURCE = Path(os.environ.get("BETAFLIGHT_SOURCE", "")).expanduser()
ROOT = Path(__file__).resolve().parents[1]


def test_versions_from_log_headers_and_msp() -> None:
    assert Version.find("Betaflight 2026.6.0-alpha (e92c10887) STM32G47X") == Version(2026, 6, 0, "alpha")
    # MSP_FC_VERSION: 2025.12+ append the version string (years since 2000 first); 4.5 sends major.minor.patch only
    assert version_from_msp(bytes([26, 6, 0, 14]) + b"2026.6.0-alpha") == Version(2026, 6, 0, "alpha")
    assert version_from_msp(bytes([4, 5, 5])) == Version(4, 5, 5)


def test_mode_bits_depend_on_the_release_line() -> None:
    # 2026.6 inserted AUTOPILOT after GPSRESCUE, so the beeper switch moved from bit 14 to 15 (fc/rc_modes.h).
    # Reading a 2026.6 log with older names would call the beeper LEDLOW, as the Blackbox Explorer does.
    flags = np.array([1 << 15])
    log_2026 = flight({"flightModeFlags": flags})
    log_2025 = flight({"flightModeFlags": flags}, headers={"Firmware revision": "Betaflight 2025.12.5 (abc) X"})
    assert log_2026["box.BEEPERON"][0] == 1
    assert log_2025["box.BEEPERON"][0] == 0 and log_2025["box.LEDLOW"][0] == 1


@pytest.mark.skipif(
    not os.environ.get("BETAFLIGHT_SOURCE") or not (SOURCE / ".git").exists(), reason="BETAFLIGHT_SOURCE not set"
)
def test_generated_tables_match_firmware_and_anchors_hold() -> None:
    """The committed tables are what the generator produces from each line's maintenance branch, and every
    firmware statement fpv-tools' logic relies on is still there (scripts/firmware_anchors.py)."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_firmware_tables.py"), str(SOURCE), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
