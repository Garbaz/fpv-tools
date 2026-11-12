"""Flight controller logic re-run on logged data (what the FC computed but did not log).

Each function cites the firmware code it reproduces; release-specific constants come from the flight's profile,
settings the log lacks from the flight's settings.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from fpv_tools.blackbox.flight import Flight

BATTERY_STATES = ("OK", "WARNING", "CRITICAL")  # batteryState_e, sensors/battery.h (BATTERY_OK = 0, ...)
BATTERY_ALERTS_HZ = 5  # TASK_BATTERY_ALERTS, fc/tasks.c: runs batteryUpdateStates()


def pt1_gain(cutoff_hz: float, dt: float) -> float:
    """pt1FilterGain(), common/filter.c."""
    omega = 2 * math.pi * cutoff_hz * dt
    return omega / (omega + 1)


def pt1(x: NDArray[np.float64], gain: float, initial: float) -> NDArray[np.float64]:
    """pt1FilterApply() over x."""
    out = np.empty(len(x))
    y = initial
    for i, v in enumerate(x):
        y += gain * (v - y)
        out[i] = y
    return out


def voltage_task_hz(flight: Flight) -> int:
    """The voltage task runs fast when sag compensation is configured (voltageMeterGenericInit, sensors/voltage.c:
    any PID profile with vbat_sag_compensation > 0, and rpm_limit off). The log shows the active profile's value;
    `--set vbat_sag_compensation=N` overrides it when another profile or rpm_limit decides."""
    slow, fast = flight.profile.voltage_task_hz
    sag = flight.settings.get("vbat_sag_compensation") or flight.headers.get("vbat_sag_compensation")
    if sag is None:
        flight.settings.require("vbat_sag_compensation", "the voltage task rate")
    return fast if int(sag or 0) > 0 else slow


def display_voltage(flight: Flight) -> NDArray[np.float64]:
    """voltageMeter.displayFiltered at the log samples.

    The FC low-passes the voltage in the voltage task with a pt1 at GET_BATTERY_LPF_FREQUENCY(period) =
    1 / (vbat_display_lpf_period / 10) Hz, and keeps the result as uint16 in 0.01 V steps, truncated (sensors/
    voltage.c, battery.h). Here the logged voltage is sampled at the task rate, filtered, and read back at the log
    samples, starting settled at the first logged voltage (the filter has run since boot).
    """
    period = flight.settings.number("vbat_display_lpf_period", "vbat_display")
    task_hz = voltage_task_hz(flight)
    t, v = flight.t, flight["vbat"]
    ticks = np.arange(t[0], t[-1] + 1 / task_hz, 1 / task_hz)
    sampled = v[np.clip(np.searchsorted(t, ticks, side="right") - 1, 0, len(v) - 1)]
    steps = np.round(sampled * 100)  # vbatLatest, 0.01 V
    filtered = np.trunc(pt1(steps, pt1_gain(1 / (period / 10), 1 / task_hz), float(steps[0]))) / 100
    return filtered[np.clip(np.searchsorted(ticks, t, side="right") - 1, 0, len(ticks) - 1)]


def battery_states(flight: Flight) -> NDArray[np.int8]:
    """batteryUpdateVoltageState() (sensors/battery.c) per log sample: 0 OK, 1 WARNING, 2 CRITICAL.

    Thresholds are cells x vbatwarning/mincellvoltage (header), entered below threshold - vbat_hysteresis; the
    vbat_duration_for_* timers count from the last run that saw the voltage above the entry threshold. The FC
    runs this BATTERY_ALERTS_HZ times a second, at a phase the log does not record: transitions here are on that
    grid from the first log sample, so they can be off by up to one period (0.2 s).
    """
    s = flight.settings
    vmin, vwarn, _ = flight.headers.cell_voltages
    hysteresis = s.number("vbat_hysteresis", "battery_state") / 100
    warn_s = s.number("vbat_duration_for_warning", "battery_state") / 10
    crit_s = s.number("vbat_duration_for_critical", "battery_state") / 10
    vf = display_voltage(flight)
    warn, crit = flight.cells * vwarn, flight.cells * vmin
    warn_hyst, crit_hyst = max(warn - hysteresis, 0), max(crit - hysteresis, 0)
    state, last_change, next_run = 0, float(flight.t[0]), float(flight.t[0])
    out = np.empty(len(vf), dtype=np.int8)
    for i, (v, now) in enumerate(zip(vf, flight.t, strict=True)):
        if now < next_run:
            out[i] = state
            continue
        next_run += 1 / BATTERY_ALERTS_HZ
        if state == 0:
            if v <= warn_hyst:
                if now - last_change >= warn_s:
                    state = 1
            else:
                last_change = now
        elif state == 1:
            if v <= crit_hyst:
                if now - last_change >= crit_s:
                    state = 2
            else:
                if v > warn:
                    state = 0
                last_change = now
        elif v > crit:
            state, last_change = 1, now
        out[i] = state
    return out
