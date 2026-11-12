"""Probes into one signal of one or more flights. Each returns plain data (dicts / lists) for printing or JSON.

`Selection` pairs flights with the samples to look at (a mask per flight), so pooling over flights and restricting
to a condition work the same for every probe.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fpv_tools.blackbox import dsp, response
from fpv_tools.blackbox.flight import Flight

type Row = dict[str, Any]


@dataclass(frozen=True)
class Part:
    flight: Flight
    mask: NDArray[np.bool_]

    @property
    def seconds(self) -> float:
        return float(self.mask.sum() / self.flight.fs)


type Selection = Sequence[Part]


def _numeric(values: NDArray[Any], name: str) -> NDArray[np.float64]:
    if values.dtype.kind not in "biuf":
        raise ValueError(f"{name} is not numeric")
    return values.astype(float)


def distribution(sel: Selection, name: str, percentiles: Sequence[float]) -> Row:
    """Min/max with the time they occur, mean, std and percentiles over the selected samples."""
    values = [(p, _numeric(p.flight[name], name)[p.mask], p.flight.t[p.mask]) for p in sel]
    x = np.concatenate([v for _, v, _ in values]) if values else np.array([])
    row: Row = {"samples": len(x), "seconds": sum(p.seconds for p in sel)}
    if not len(x):
        return row
    lo = min(((p, v, t) for p, v, t in values if len(v)), key=lambda e: e[1].min())
    hi = max(((p, v, t) for p, v, t in values if len(v)), key=lambda e: e[1].max())
    row |= {
        "min": float(x.min()),
        "min_at": {"flight": lo[0].flight.name, "t": float(lo[2][np.argmin(lo[1])])},
        "max": float(x.max()),
        "max_at": {"flight": hi[0].flight.name, "t": float(hi[2][np.argmax(hi[1])])},
        "mean": float(x.mean()),
        "std": float(x.std()),
        "percentiles": {f"{q:g}": float(np.percentile(x, q)) for q in percentiles},
    }
    return row


def time_beyond(sel: Selection, name: str, below: Sequence[float], above: Sequence[float]) -> Row:
    """Seconds the signal spends below / above each threshold within the selection."""
    out: Row = {}
    for v in below:
        out[f"<{v:g}"] = sum(float((_numeric(p.flight[name], name)[p.mask] < v).sum() / p.flight.fs) for p in sel)
    for v in above:
        out[f">{v:g}"] = sum(float((_numeric(p.flight[name], name)[p.mask] > v).sum() / p.flight.fs) for p in sel)
    return out


def runs(sel: Selection, name: str, min_seconds: float = 0.0) -> list[Row]:
    """The selection's contiguous periods, with the signal's range in each."""
    out: list[Row] = []
    for p in sel:
        values = p.flight[name]
        for s, e in dsp.runs(p.mask):
            t0, t1 = float(p.flight.t[s]), float(p.flight.t[e - 1])
            if t1 - t0 < min_seconds:
                continue
            row: Row = {"flight": p.flight.name, "start": t0, "end": t1, "seconds": t1 - t0}
            if values.dtype.kind in "biuf":
                row |= {"min": float(values[s:e].min()), "max": float(values[s:e].max())}
            else:
                row["values"] = list(dict.fromkeys(values[s:e].tolist()))
            out.append(row)
    return out


def changes(sel: Selection, name: str) -> list[Row]:
    """Every change of value within the selection (for discrete signals: mode, failsafe, rx, box.*): the value at
    the first selected sample, then each selected sample whose value differs from the previous selected one (a
    change while the selection was off shows where it resumes)."""
    out: list[Row] = []
    for p in sel:
        idx = np.flatnonzero(p.mask)
        if not len(idx):
            continue
        values, t = p.flight[name][idx], p.flight.t[idx]
        out.append({"flight": p.flight.name, "t": float(t[0]), "from": None, "to": _item(values[0])})
        for k in np.flatnonzero(values[1:] != values[:-1]) + 1:
            out.append(
                {"flight": p.flight.name, "t": float(t[k]), "from": _item(values[k - 1]), "to": _item(values[k])}
            )
    return out


def _item(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def spectrum(
    sel: Selection,
    name: str,
    *,
    peaks: int = 3,
    min_hz: float = 20.0,
    separation: float = 30.0,
    bands: Sequence[tuple[float, float | None]] = (),
    nperseg: int = 256,
) -> Row:
    """Welch spectrum over the selection: strongest peaks and RMS within bands (in the signal's units)."""
    result = _pooled_psd(sel, name, nperseg)
    if result is None:
        return {"seconds": 0.0}
    freqs, power, seconds = result
    return {
        "seconds": seconds,
        "resolution_hz": float(freqs[1] - freqs[0]),
        "peaks": [{"hz": hz, "rms": rms} for hz, rms in dsp.peaks_of(freqs, power, min_hz, peaks, separation)],
        "bands": [{"from_hz": lo, "to_hz": hi, "rms": dsp.band_rms_of(freqs, power, lo, hi)} for lo, hi in bands],
    }


def _pooled_psd(
    sel: Selection, name: str, nperseg: int
) -> tuple[NDArray[np.float64], NDArray[np.float64], float] | None:
    """PSDs of each flight's selection, averaged weighted by duration. Flights logged at slightly different
    rates get different frequency grids; each PSD is interpolated onto the grid of the longest part."""
    parts: list[tuple[NDArray[np.float64], NDArray[np.float64], float]] = []
    for p in sel:
        result = dsp.psd_masked(_numeric(p.flight[name], name), p.flight.fs, p.mask, nperseg)
        if result is not None:
            parts.append(result)
    if not parts:
        return None
    grid = max(parts, key=lambda part: part[2])[0]
    total = sum(seconds for _, _, seconds in parts)
    power = sum(np.interp(grid, freqs, pw) * seconds for freqs, pw, seconds in parts) / total
    return grid, np.asarray(power, dtype=float), total


def vs_throttle(
    sel: Selection, name: str, *, step: float = 10.0, min_hz: float = 20.0, nperseg: int = 256
) -> list[Row]:
    """Spectrum per throttle band, as the Blackbox Explorer's frequency-vs-throttle view: per band of `step` %
    mixer throttle, the time spent there, the strongest peak and the RMS above `min_hz`."""
    out: list[Row] = []
    for lo in np.arange(0, 100, step):
        hi = lo + step
        band = [Part(p.flight, p.mask & (p.flight["thr"] >= lo) & (p.flight["thr"] < hi)) for p in sel]
        result = _pooled_psd(band, name, nperseg)
        row: Row = {"thr_from": float(lo), "thr_to": float(hi), "seconds": sum(b.seconds for b in band)}
        if result is not None:
            freqs, power, used = result
            peak = dsp.peaks_of(freqs, power, min_hz, 1, 0)
            row |= {"analysed_s": used, "rms": dsp.band_rms_of(freqs, power, min_hz)}
            if peak:
                row |= {"peak_hz": peak[0][0], "peak_rms": peak[0][1]}
        out.append(row)
    return out


def step_response(
    sel: Selection,
    name: str,
    reference: str,
    *,
    window: float = 1.0,
    min_amplitude: float = 20.0,
    settle: tuple[float, float] = (0.15, 0.3),
    split: float | None = None,
) -> list[Row]:
    """Step response of `name` to `reference` (e.g. gyro.roll to setpoint.roll) by deconvolution over windows
    where the selection holds throughout; pooled over flights. The response runs to the end of `settle`, the time
    after the step (s) taken as steady state. With `split`, windows whose reference swings below / above it are
    reported separately (small vs large moves)."""
    if not 0 <= settle[0] < settle[1] < window:
        raise ValueError(
            f"the settle window ({settle[0]:g}-{settle[1]:g} s) must start before it ends, and end within the "
            f"response window ({window:g} s)"
        )
    windows = [
        w
        for p in sel
        for w in response.step_responses(
            p.flight, name, reference, p.mask, window=window, length=settle[1], min_amplitude=min_amplitude
        )
    ]
    groups = (
        {"all": windows}
        if split is None
        else {
            f"<={split:g}": [w for w in windows if w[0] <= split],
            f">{split:g}": [w for w in windows if w[0] > split],
        }
    )
    out: list[Row] = []
    for label, members in groups.items():
        metrics = response.step_metrics((curve for _, curve in members), settle)
        row: Row = {"reference_swing": label, "windows": len(members)}
        if metrics:
            row |= {
                "steady_gain": metrics.steady_gain,
                "t50_ms": metrics.t50_ms,
                "t90_ms": metrics.t90_ms,
                "peak": metrics.peak,
                "peak_ms": metrics.peak_ms,
                "curve": metrics.curve.tolist(),
            }
        out.append(row)
    return out
