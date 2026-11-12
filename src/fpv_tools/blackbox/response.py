"""How gyro follows setpoint: step response estimates and per-move metrics for stick snaps and releases."""

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from fpv_tools.blackbox.flight import Flight

GRID_MS = 1.0


def step_responses(
    flight: Flight,
    name: str,
    reference: str,
    mask: NDArray[np.bool_],
    window: float = 1.0,
    length: float = 0.3,
    min_amplitude: float = 20.0,
) -> list[tuple[float, NDArray[np.float64]]]:
    """Step response reference -> name by Wiener deconvolution over overlapping windows (as PID-Analyzer does).

    Only windows where `mask` holds throughout and the reference swings at least `min_amplitude` are used.
    Returns [(reference swing, response on a 1 ms grid of `length` s)].
    """
    fs = flight.fs
    n, keep = int(window * fs), int(length * fs)
    sp, gy = flight[reference].astype(float), flight[name].astype(float)
    taper = np.hanning(n)
    grid = np.arange(0, length, GRID_MS / 1e3)
    out: list[tuple[float, NDArray[np.float64]]] = []
    for start in range(0, len(sp) - n, n // 4):
        if not mask[start : start + n].all():
            continue
        x, y = sp[start : start + n], gy[start : start + n]
        amplitude = float(np.abs(x - x.mean()).max())
        if amplitude < min_amplitude:
            continue
        X = np.fft.rfft((x - x.mean()) * taper, 2 * n)
        Y = np.fft.rfft((y - y.mean()) * taper, 2 * n)
        H = Y * np.conj(X) / (np.abs(X) ** 2 + 1e-3 * np.mean(np.abs(X) ** 2) + 1e-9)
        step = np.cumsum(np.fft.irfft(H)[:keep])
        out.append((amplitude, np.interp(grid, np.arange(keep) / fs, step)))
    return out


@dataclass(frozen=True)
class StepMetrics:
    """Rise times and peak are relative to the steady state; without a positive steady state they do not exist."""

    count: int
    steady_gain: float
    t50_ms: float | None  # first reaching 50 % of the steady state; None if it never does
    t90_ms: float | None
    peak: float | None  # largest value before the settle window, as a multiple of the steady state
    peak_ms: float | None
    curve: np.ndarray  # median response on the 1 ms grid, per unit of reference step (1 = follows exactly)


def step_metrics(responses: Iterable[np.ndarray], settle: tuple[float, float]) -> StepMetrics | None:
    """Median response and its metrics; `settle` is the time after the step (s) whose median is the steady state."""
    curves = np.array(list(responses))
    if not len(curves):
        return None
    median = np.median(curves, axis=0)
    lo, hi = (round(s * 1e3 / GRID_MS) for s in settle)
    gain = float(np.median(median[lo:hi]))
    if gain <= 0:
        return StepMetrics(len(curves), gain, None, None, None, None, median)

    def first(level: float) -> float | None:
        above = np.flatnonzero(median >= level * gain)
        return float(above[0] * GRID_MS) if len(above) else None

    peak_at = int(np.argmax(median[:lo]))
    return StepMetrics(
        count=len(curves),
        steady_gain=gain,
        t50_ms=first(0.5),
        t90_ms=first(0.9),
        peak=float(median[peak_at] / gain),
        peak_ms=peak_at * GRID_MS,
        curve=median,
    )
