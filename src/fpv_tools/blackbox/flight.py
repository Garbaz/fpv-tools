"""One decoded flight (one arm-to-disarm log) with named signals in physical units (reference.SIGNALS
lists them for users).

Beyond that list: P/I/D/F are 0 where the firmware logs no column (zero gain); rx, boxes and box.NAME are NaN,
mode and failsafe "", where no slow frame had been logged yet (the first frame of a log).

Settings the log does not record come from `settings`; a signal that needs one nobody provided raises
SettingMissing, which says how to provide it.
"""

from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fpv_tools.blackbox.bbl import Log
from fpv_tools.blackbox.headers import Headers
from fpv_tools.firmware import Profile, Version, profile
from fpv_tools.settings import Settings

type Array = NDArray[Any]

AXES = ("roll", "pitch", "yaw")


class SignalError(KeyError):
    def __str__(self) -> str:
        return str(self.args[0])


def _name(table: tuple[str, ...], value: float) -> str:
    """table[value], "" for NaN (unknown), the number itself if the table has no such entry."""
    if np.isnan(value):
        return ""
    return table[int(value)] if 0 <= value < len(table) else str(int(value))


def axis_index(axis: str) -> int:
    if axis in AXES:
        return AXES.index(axis)
    raise SignalError(f"unknown axis {axis!r}; use roll, pitch or yaw")


class Flight:
    def __init__(
        self,
        log: Log,
        *,
        source: Path | None = None,
        index: int = 1,
        settings: Settings | None = None,
        ignore_version: bool = False,
    ) -> None:
        self.log = log
        self.fields: dict[str, NDArray[Any]] = {**log.main, **log.slow}
        self.headers = Headers(log.headers)
        self.log_events: list[dict[str, Any]] = list(log.events)
        self.source = source
        self.index = index
        self.settings = settings or Settings()
        self.version = Version.find(self.headers.firmware)
        log_name = source.name if source else "log"  # one warning per file, not per flight
        self.profile: Profile = profile(self.version, source=log_name, ignore_mismatch=ignore_version)
        self.t_us: NDArray[np.int64] = np.asarray(log.main["time"], dtype=np.int64)
        self.t: NDArray[np.float64] = (self.t_us - self.t_us[0]) / 1e6
        self.fs = 1 / float(np.median(np.diff(self.t))) if len(self.t) > 1 else float("nan")
        self.motor_count = sum(1 for name in log.main if name.startswith("motor["))
        self._cache: dict[str, Array] = {}

    @property
    def name(self) -> str:
        return f"{self.source.name if self.source else 'flight'}:{self.index}"

    @property
    def duration(self) -> float:
        return float(self.t[-1]) if len(self.t) else 0.0

    def __len__(self) -> int:
        return len(self.t)

    def __repr__(self) -> str:
        return f"<Flight {self.name} {self.duration:.1f}s {len(self)} frames @ {self.fs:.0f} Hz>"

    # --- signals ------------------------------------------------------------------------------------------

    def __getitem__(self, name: str) -> Array:
        if name not in self._cache:
            self._cache[name] = self._signal(name)
        return self._cache[name]

    def signal_names(self) -> list[str]:
        """Named signals this flight provides from the log alone."""
        names = ["t", "thr", "rc_thr", "sticks", "acc", "jerk", "vbat", "amps", "mah", "rssi", "rx"]
        names += ["mode", "failsafe", "boxes", "motor.avg", "motor.min", "motor.max"]
        names += [f"{k}.{a}" for k in ("gyro", "gyro_raw", "setpoint", "error", "rc", "P", "I", "D", "F") for a in AXES]
        names += [f"acc.{x}" for x in "xyz"] + [f"motor.{i}" for i in range(self.motor_count)]
        names += [f"debug.{i}" for i in range(8) if f"debug[{i}]" in self.fields]
        return [n for n in names if self._available(n)]

    def _available(self, name: str) -> bool:
        try:
            self[name]
        except KeyError:
            return False
        return True

    def _col(self, column: str) -> NDArray[np.float64]:
        if column not in self.fields:
            raise SignalError(f"{self.name}: log has no {column!r} field")
        return self.fields[column].astype(float)

    def _signal(self, name: str) -> Array:
        from fpv_tools.blackbox import fcmodel

        kind, _, sub = name.partition(".")
        h, p = self.headers, self.profile
        rate_scale = p.blackbox_high_resolution_scale if h.get("blackbox_high_resolution") == "1" else 1.0
        match kind, sub:
            case "t", "":
                return self.t
            case (("gyro" | "gyro_raw" | "setpoint" | "rc"), axis) if axis:
                column = {"gyro": "gyroADC", "gyro_raw": "gyroUnfilt", "setpoint": "setpoint", "rc": "rcCommand"}[kind]
                return self._col(f"{column}[{axis_index(axis)}]") / rate_scale
            case "error", axis if axis:
                return self[f"setpoint.{axis}"] - self[f"gyro.{axis}"]
            case (("P" | "I" | "D" | "F"), axis) if axis:
                column = f"axis{kind}[{axis_index(axis)}]"
                # blackbox.c leaves out D/F columns for axes whose gain is zero: the term is 0 there
                return self._col(column) if column in self.fields else np.zeros(len(self))
            case "thr", "":
                return self._col("setpoint[3]") / 10  # mixerGetThrottle() * 1000, blackbox.c
            case "rc_thr", "":
                return (self._col("rcCommand[3]") - 1000) / 10
            case "sticks", "":
                return np.max([np.abs(self[f"rc.{a}"]) for a in AXES], axis=0) / 5
            case "motor", ("avg" | "min" | "max"):
                motors = np.stack([self[f"motor.{i}"] for i in range(self.motor_count)])
                return {"avg": np.mean, "min": np.min, "max": np.max}[sub](motors, axis=0)
            case "motor", index if index.isdigit():
                lo, hi = h.motor_output
                return (self._col(f"motor[{index}]") - lo) / (hi - lo) * 100
            case "acc", axis if axis in ("x", "y", "z"):
                return self._col(f"accSmooth[{'xyz'.index(axis)}]") / h.acc_1g
            case "acc", "":
                return np.linalg.norm([self["acc.x"], self["acc.y"], self["acc.z"]], axis=0)
            case "jerk", "":
                acc = np.stack([self["acc.x"], self["acc.y"], self["acc.z"]])
                return np.linalg.norm(np.gradient(acc, self.t, axis=1), axis=0)
            case "vbat", "":
                return self._col("vbatLatest") / 100  # getBatteryVoltageLatest(): 0.01 V steps (sensors/voltage.h)
            case "cell", "":
                return self["vbat"] / self.cells
            case "amps", "":
                return self._amps()
            case "mah", "":
                return np.cumsum(self["amps"] * np.diff(self.t, prepend=self.t[0])) / 3.6
            case "rssi", "":
                return self._col("rssi")
            case "dbm", "":
                return self._dbm()
            case "rx", "":
                return self._col("rxSignalReceived")
            case "boxes", "":
                return self._col("flightModeFlags")
            case "box", box if box:
                if box not in p.box_ids:
                    raise SignalError(f"unknown mode switch {box!r}; known: {', '.join(p.box_ids)}")
                boxes = self["boxes"]
                known = ~np.isnan(boxes)
                out = np.full(len(boxes), np.nan)
                out[known] = (boxes[known].astype(np.int64) >> p.box_ids.index(box)) & 1
                return out
            case "mode", "":
                return self._modes()
            case "failsafe", "":
                phases = self._col("failsafePhase")
                return np.array([_name(p.failsafe_phases, v) for v in phases])
            case "debug", index if index.isdigit():
                return self._col(f"debug[{index}]")
            case "vbat_display", "":
                return fcmodel.display_voltage(self)
            case "battery_state", "":
                return np.array(fcmodel.BATTERY_STATES)[fcmodel.battery_states(self)]
            case _ if name in self.fields:
                return self.fields[name]
            case _:
                raise SignalError(f"unknown signal {name!r} (`fpv bb info --signals` lists them)")

    def _modes(self) -> Array:
        p = self.profile
        bits = [(p.box_ids.index(b), b) for b in p.box_ids if b in p.flight_mode_boxes]
        labels: dict[int, str] = {}
        out: list[str] = []
        for value in self["boxes"]:
            if np.isnan(value):
                out.append("")  # no slow frame logged yet
                continue
            key = int(value)
            if key not in labels:
                labels[key] = "+".join(b for bit, b in bits if key >> bit & 1) or "ACRO"
            out.append(labels[key])
        return np.array(out)

    def _amps(self) -> NDArray[np.float64]:
        amps = self._col("amperageLatest") / 100  # getAmperageLatest(): centiamperes (sensors/current.h)
        target = self.settings.get("ibata_scale")
        if target is None or "currentSensor" not in self.headers:
            return amps  # as logged; the header has the scale only for the ADC current meter (blackbox.c)
        offset, scale = self.headers.current_sensor
        if int(target) == scale:
            return amps
        if offset:
            raise SignalError(f"{self.name}: current sensor offset {offset} mA, rescaling needs offset 0")
        # A = mV * 10 / scale + offset / 1000 (sensors/current.c): with offset 0 a new scale is a plain factor
        return amps * scale / int(target)

    def _dbm(self) -> NDArray[np.float64]:
        """CRSF uplink RSSI. The FC takes RSSI from CRSF link statistics unless the RSSI_ADC feature is on or
        rssi_channel is set (rx/rx.c rxInit, rx/crsf.c crsfRxInit); rssi = scaleRange(dBm, min, max, 0, RSSI_MAX)."""
        p = self.profile
        if "serialrx_provider" in self.headers:  # logged since 2026.6
            provider = p.serialrx_providers[self.headers.number("serialrx_provider")]
        else:
            provider = self.settings.require("serialrx_provider", "dbm").upper()
        if provider != "CRSF":
            raise SignalError(f"{self.name}: receiver protocol is {provider}, not CRSF")
        if self.headers.number("features") & p.feature_bits["RSSI_ADC"]:
            raise SignalError(f"{self.name}: the RSSI_ADC feature is on, rssi comes from the ADC")
        if self.settings.number("rssi_channel", "dbm") != 0:
            raise SignalError("rssi_channel is set: rssi comes from an RC channel, not CRSF link statistics")
        # The log has getRssi() (rx/rx.c): the CRSF value inverted, scaled and offset by these settings
        scaling = tuple(
            self.settings.require(key, "dbm").upper() for key in ("rssi_invert", "rssi_scale", "rssi_offset")
        )
        if scaling != ("OFF", "100", "0"):
            given = ", ".join(scaling)
            raise SignalError(
                f"rssi is rescaled (rssi_invert, rssi_scale, rssi_offset = {given}): dBm is not recoverable"
            )
        # The FC truncates (hi - lo) / rssi_max * (dBm - lo) to an integer; for whole dBm, as CRSF reports, the
        # inverse is exact: the smallest dBm whose image reaches the logged value. The logged rssi is low-passed
        # (rssi_smoothing, rx/rx.c updateRSSI), so changes show here with that lag and in between values.
        lo, hi = p.crsf_rssi_dbm_range
        return np.ceil(self["rssi"] * (hi - lo) / p.rssi_max - 1e-9) + lo

    @property
    def cells(self) -> int:
        """force_battery_cell_count if set, else the FC's autoDetectCellCount() (sensors/battery.c) on the voltage
        at log start (vbatref): voltage / vbatmaxcellvoltage + 1, in 0.01 V units."""
        if forced := self.settings.number("force_battery_cell_count", "the cell count"):
            return forced
        vmax = self.headers.numbers("vbatcellvoltage")[2]
        return self.headers.number("vbatref") // vmax + 1

    def between(self, start: float | None = None, end: float | None = None) -> NDArray[np.bool_]:
        lo = start if start is not None else -np.inf
        hi = end if end is not None else np.inf
        return (self.t >= lo) & (self.t <= hi)
