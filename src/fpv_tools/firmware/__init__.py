"""Release-line-specific firmware knowledge.

A profile combines, for one Betaflight release line (4.5, 2025.12, 2026.6):
  - name tables generated from that line's maintenance branch (tables/, scripts/gen_firmware_tables.py), which also
    checks the firmware statements fpv-tools' own logic reproduces (scripts/firmware_anchors.py) and refuses to
    write a line where any of them fails;
  - MSP payload layouts and what their values mean (msp_layouts.py), compared by hand across the lines.

Logs and FCs are matched to a line by their firmware version. Patch releases share their line's profile; pre-release
builds (alpha, RC) map to their line with a warning, because their tables can differ from the release (2026.6.0-alpha
builds number the debug modes from index 96 differently).
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from functools import cached_property
from types import ModuleType
from typing import TYPE_CHECKING, NamedTuple

from fpv_tools.firmware.tables import betaflight_4_5, betaflight_2025_12, betaflight_2026_6

if TYPE_CHECKING:
    from fpv_tools.firmware.layout import MspLayout

TABLES: dict[tuple[int, int], ModuleType] = {
    (4, 5): betaflight_4_5,
    (2025, 12): betaflight_2025_12,
    (2026, 6): betaflight_2026_6,
}


class FirmwareError(Exception):
    pass


class Version(NamedTuple):
    major: int
    minor: int
    patch: int
    prerelease: str = ""

    @classmethod
    def find(cls, text: str) -> Version | None:
        """The first version in text, e.g. "2026.6.0-alpha" in "Betaflight 2026.6.0-alpha (e92c10887) STM32G47X"."""
        if m := re.search(r"\b(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?", text):
            return cls(int(m[1]), int(m[2]), int(m[3]), m[4] or "")
        return None

    @property
    def line(self) -> tuple[int, int]:
        return self.major, self.minor

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}" + (f"-{self.prerelease}" if self.prerelease else "")


@dataclass(frozen=True)
class Profile:
    line: tuple[int, int]
    box_ids: tuple[str, ...]  # bit n of the log's flightModeFlags (rcModeActivationMask) is box_ids[n]
    flight_mode_boxes: frozenset[str]  # boxes that select a flight mode; none active = ACRO
    failsafe_phases: tuple[str, ...]
    debug_modes: tuple[str, ...]  # indexed by the debug_mode header
    debug_mode_aliases: dict[str, tuple[str, ...]]  # current name -> names older builds of the line used
    serialrx_providers: tuple[str, ...]  # indexed by the serialrx_provider setting/header
    disarm_reasons: tuple[str, ...]  # indexed by the reason in DISARM log events
    adjustment_functions: tuple[str, ...]  # indexed by the function in INFLIGHT_ADJUSTMENT log events
    feature_bits: dict[str, int]
    api_minor: int  # MSP API version 1.N of the line
    crsf_rssi_dbm_range: tuple[int, int]
    rssi_max: int
    voltage_task_hz: tuple[int, int]  # (slow, fast with sag compensation configured)
    blackbox_high_resolution_scale: float  # blackbox.c, anchored
    rc_channels: tuple[str, ...]  # by rcData index
    aux_channels: tuple[str, ...]  # by aux channel index (mode and VTX ranges)
    permanent_box_ids: dict[int, str]  # box (as in box_ids) by the permanent ID MSP numbers modes with
    vtx_device_types: dict[int, str]
    vtx_low_power_disarm: tuple[str, ...]  # the vtx_low_power_disarm setting's names, by value
    flashfs_flags: dict[int, str]  # MSP_DATAFLASH_SUMMARY flag bits
    channel_range_min: int  # us at mode range step 0; steps are 25 us (MODE_STEP_TO_CHANNEL_VALUE, anchored)

    @cached_property
    def msp(self) -> dict[str, MspLayout]:
        from fpv_tools.firmware import msp_layouts  # construct takes a while to import: only when MSP needs it

        return msp_layouts.layouts(self)

    @property
    def name(self) -> str:
        return f"{self.line[0]}.{self.line[1]}"


def _profile(line: tuple[int, int]) -> Profile:
    t = TABLES[line]
    # rc_alias_e names the channels up to AUX12; the firmware supports MAX_SUPPORTED_RC_CHANNEL_COUNT, numbering
    # the rest on in the same way (aux channel n is rcData[NON_AUX_CHANNEL_COUNT + n - 1], rx.h, anchored)
    channels = t.RC_CHANNELS + tuple(
        f"AUX{i - t.NON_AUX_CHANNEL_COUNT + 1}" for i in range(len(t.RC_CHANNELS), t.MAX_SUPPORTED_RC_CHANNEL_COUNT)
    )
    return Profile(
        line=line,
        box_ids=t.BOX_IDS,
        flight_mode_boxes=frozenset(t.BOX_IDS[1 : t.BOXID_FLIGHTMODE_LAST + 1]),  # ARM is box 0
        failsafe_phases=t.FAILSAFE_PHASES,
        debug_modes=t.DEBUG_MODES,
        debug_mode_aliases=t.ALIASES,
        serialrx_providers=t.SERIALRX_PROVIDERS,
        disarm_reasons=t.DISARM_REASONS,
        adjustment_functions=t.ADJUSTMENT_FUNCTIONS,
        feature_bits=t.FEATURES,
        api_minor=t.API_VERSION_MINOR,
        crsf_rssi_dbm_range=(t.CRSF_RSSI_MIN, t.CRSF_RSSI_MAX),
        rssi_max=t.RSSI_MAX_VALUE,
        voltage_task_hz=(t.SLOW_VOLTAGE_TASK_FREQ_HZ, t.FAST_VOLTAGE_TASK_FREQ_HZ),
        blackbox_high_resolution_scale=10.0,
        rc_channels=channels,
        aux_channels=channels[t.NON_AUX_CHANNEL_COUNT :],
        permanent_box_ids=t.PERMANENT_BOX_IDS,
        vtx_device_types=t.VTX_DEVICE_TYPES,
        vtx_low_power_disarm=t.VTX_LOW_POWER_DISARM,
        flashfs_flags=t.FLASHFS_FLAGS,
        channel_range_min=t.CHANNEL_RANGE_MIN,
    )


PROFILES = {line: _profile(line) for line in TABLES}


def supported() -> list[str]:
    return [p.name for _, p in sorted(PROFILES.items())]


def profile(version: Version | None, *, source: str, ignore_mismatch: bool = False) -> Profile:
    """The profile of `version`'s release line (found in `source`, for messages).

    Unknown or missing version: an error, or with ignore_mismatch a warning and the newest profile.
    Pre-release builds: their line's profile, with a warning.
    """
    newest = PROFILES[max(PROFILES)]
    if version is not None and version.line in PROFILES:
        if version.prerelease:
            warnings.warn(
                f"{source} is a pre-release build ({version}); names are those of the "
                f"{version.line[0]}.{version.line[1]} release branch and can differ for this build",
                stacklevel=2,
            )
        return PROFILES[version.line]
    found = f"Betaflight {version}" if version else "an unknown firmware version"
    message = f"{source} is from {found}; fpv-tools supports {', '.join(supported())}"
    if not ignore_mismatch:
        raise FirmwareError(f"{message} (--ignore-version to use {newest.name} anyway)")
    warnings.warn(f"{message}; using {newest.name}, names and semantics may be wrong", stacklevel=2)
    return newest


def version_from_msp(reply: bytes) -> Version | None:
    """The firmware version from an MSP_FC_VERSION reply: 2025.12+ append the version string (with any
    pre-release tag); 4.5 sends only major, minor, patch."""
    if len(reply) > 4 and len(reply) >= 4 + reply[3]:
        return Version.find(reply[4 : 4 + reply[3]].decode("ascii", "replace"))
    if len(reply) >= 3 and reply[0] < 20:  # 4.x: major.minor.patch (2025.12+ send years since 2000, >= 25)
        return Version(reply[0], reply[1], reply[2])
    return None
