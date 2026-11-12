"""`fpv-tools bb`: blackbox logs.

numpy, the decoder and the FC link are imported where a command needs them, so that `--help` is quick.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import App, Parameter

from fpv_tools.app.common import IGNORE_VERSION, LogOptions, Port, Timeout, confirm, transfer, waiting
from fpv_tools.blackbox.reference import CONDITIONS, SIGNALS
from fpv_tools.output import finite, number, table, write_csv, write_json

if TYPE_CHECKING:
    from fpv_tools.blackbox import probes
    from fpv_tools.blackbox.flight import Flight
    from fpv_tools.fc.msp import MspClient

app = App(
    name="bb",
    group_parameters="Options",
    help=f"""Blackbox logs: pull them off the FC, see what they contain, probe signals, export data.

Logs are given as FILE, FILE:N (its flight N), FILE:N-M or a directory of logs.

{SIGNALS}
{CONDITIONS}""",
)

Log = Annotated[str, Parameter(required=True, help="FILE, FILE:N (its flight N), FILE:N-M or a directory of logs.")]
Seconds = Annotated[float | None, Parameter(metavar="SECONDS")]


def _short_names(flights: list[Flight]) -> dict[str, str]:
    """Flight labels for text output: "#N" when all flights come from one file, else "FILE:N"."""
    if len({f.source for f in flights}) == 1:
        return {f.name: f"#{f.index}" for f in flights}
    return {f.name: f.name for f in flights}


def _shorten(value: Any, names: dict[str, str]) -> Any:
    """Replace full flight names by their short labels in a (nested) result, for text output."""
    match value:
        case dict():
            items: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
            return {k: names.get(v, v) if k in ("flight", "flights") else _shorten(v, names) for k, v in items.items()}
        case list():
            entries: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
            return [_shorten(v, names) for v in entries]
        case _:
            return value


def _flights(logs: tuple[str, ...], options: LogOptions) -> list[Flight]:
    from fpv_tools.blackbox.decoder import load

    if not logs:
        raise ValueError("no logs given")
    flights = load(logs, settings=options.settings(), ignore_version=options.ignore_version)
    if not flights:
        raise ValueError("no flights in the given logs")
    return flights


# --- pull, erase ------------------------------------------------------------------------------------------------


@app.command(usage="Usage: fpv-tools bb pull [OPTIONS] [FILE]")
def pull(
    file: Annotated[Path | None, Parameter(allow_leading_hyphen=True)] = None,
    /,
    *,
    output_dir: Annotated[Path | None, Parameter(name=["--output-dir", "-O"], metavar="DIR")] = None,
    erase: bool = False,
    port: Port = None,
    timeout: Timeout = 2.0,
) -> None:
    """Copy the blackbox log off the FC's onboard flash into a file.

    Parameters
    ----------
    file
        File to write, or - for stdout. Default: named as the Betaflight app names it, e.g.
        BTFL_BLACKBOX_LOG_CRAFT_20261109_143000_BOARD.BBL.
    output_dir
        Directory for the default-named file.
    erase
        Erase the flash after a complete read.
    """
    from fpv_tools.fc import dataflash
    from fpv_tools.fc.link import open_port
    from fpv_tools.fc.msp import MspClient

    if file is not None and output_dir is not None:
        raise ValueError("FILE and --output-dir exclude each other")
    with open_port(port, timeout) as serial, MspClient(serial) as fc:
        with transfer("reading") as update:
            data = dataflash.read(fc, update)
        if file == Path("-"):
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
        else:
            target = file or (output_dir or Path()) / _log_name(fc)
            target.write_bytes(data)
            print(target)
        if erase:
            with waiting("erasing"):
                dataflash.erase(fc)
            print("flash erased", file=sys.stderr)


@app.command
def erase(*, yes: Annotated[bool, Parameter(alias="-y")] = False, port: Port = None, timeout: Timeout = 2.0) -> None:
    """Erase the FC's onboard flash, deleting the logs on it.

    Parameters
    ----------
    yes
        Do not ask for confirmation.
    """
    from fpv_tools.fc import dataflash
    from fpv_tools.fc.link import open_port
    from fpv_tools.fc.msp import MspClient

    with open_port(port, timeout) as serial, MspClient(serial) as fc:
        if not yes and not confirm(f"Erase {dataflash.summary(fc).used_size / 1e6:.1f} MB of logs on the FC's flash?"):
            return
        with waiting("erasing"):
            dataflash.erase(fc)
    print("flash erased", file=sys.stderr)


def _log_name(fc: MspClient) -> str:
    """{variant}_BLACKBOX_LOG_{CRAFT}_{yyyymmdd}_{hhmmss}_{board}.BBL, as the Betaflight app's generateFilename()."""
    from fpv_tools.fc.msp_codes import CODES

    variant = fc.request(CODES["MSP_FC_VARIANT"]).decode("ascii", "replace").strip("\0") or "UNKNOWN"
    craft = re.sub(r"\s+", "_", fc.request(CODES["MSP_NAME"]).decode("ascii", "replace").strip("\0").strip()).upper()
    board_info = fc.request(CODES["MSP_BOARD_INFO"])
    target_len = board_info[8]
    board_at = 9 + target_len
    board = (
        board_info[board_at + 1 : board_at + 1 + board_info[board_at]].decode("ascii", "replace")
        if len(board_info) > board_at
        else ""
    )
    name = f"{variant}_BLACKBOX_LOG" + (f"_{craft}" if craft else "") + datetime.now().strftime("_%Y%m%d_%H%M%S")
    return name + (f"_{board}" if board else "") + ".BBL"


# --- info ------------------------------------------------------------------------------------------------------


@app.command
def info(
    *logs: Log,
    headers: Annotated[str | None, Parameter(name="--headers", metavar="REGEX")] = None,
    signals: bool = False,
    json: bool = False,
    ignore_version: Annotated[bool, Parameter(help=IGNORE_VERSION)] = False,
) -> None:
    """What logs contain: firmware, craft, flights with timing, events and headers.

    Parameters
    ----------
    headers
        Print headers whose name matches this regex ('.' for all).
    signals
        List the signals each flight provides (except those that need FC settings).
    json
        Everything as one JSON document.
    """
    from fpv_tools.blackbox import overview

    flights = _flights(logs, LogOptions(ignore_version=ignore_version))
    files = overview.describe(flights)
    if signals:
        for f, entry in zip(flights, (fl for file in files for fl in file["flights"]), strict=True):
            entry["signals"] = f.signal_names()
    if json:
        write_json(finite(files))
        return
    pattern = re.compile(headers) if headers is not None else None
    for file in files:
        print(f"{file['file']}\n  {file['firmware']}, {file['board']}, craft {file['craft']!r}")
        table(
            [
                {
                    "flight": fl["index"],
                    "power-up": fl["power_up"],
                    "duration_s": fl["duration_s"],
                    "rate_hz": fl["rate_hz"],
                    "frames": fl["frames"],
                    "debug_mode": fl["debug_mode"],
                    "events": len(fl["events"]),
                    "truncated": bool(fl["decoding"].get("truncated_end")),
                    "corrupt_frames": fl["decoding"].get("corrupt", 0),
                }
                for fl in file["flights"]
            ]
        )
        for fl in file["flights"]:
            if fl["events"]:
                print(f"\nflight {fl['index']} events:")
                table([{k: v for k, v in e.items()} for e in fl["events"]])
        if signals:
            for fl in file["flights"]:
                print(f"\nflight {fl['index']} signals: {' '.join(fl['signals'])}")
        if pattern:
            shown = {k: v for k, v in file["headers"].items() if pattern.search(k)}
            print()
            table([{"header": k, "value": v} for k, v in shown.items()])
            differing = {
                (fl["index"], k): v for fl in file["flights"] for k, v in fl["headers"].items() if pattern.search(k)
            }
            if differing:
                print("\nper flight:")
                table([{"flight": i, "header": k, "value": v} for (i, k), v in differing.items()])
        print()


# --- probes: stats, runs, changes, spectrum, step ---------------------------------------------------------------


type Row = dict[str, Any]


@Parameter(name="*")
@dataclass(frozen=True)
class Probe:
    """The signals to probe and the samples to look at, shared by the probe commands."""

    signal: Annotated[tuple[str, ...], Parameter(name=["--signal", "-s"], metavar="NAME")]
    "Signal to probe, e.g. gyro.roll, thr, mode (`fpv-tools bb --help` lists them); repeatable."

    when: Annotated[str | None, Parameter(metavar="EXPR")] = None
    """Only samples where this condition holds, e.g. 'thr > 5 and mode == "ACRO"' (`fpv-tools bb --help` has the
    syntax)."""

    start: Annotated[Seconds, Parameter(name="--from")] = None
    "Only from this time (s since the flight's first frame)."

    end: Annotated[Seconds, Parameter(name="--to")] = None
    "Only up to this time."

    pool: bool = False
    "Combine all flights into one result instead of one per flight."

    json: bool = False
    "Results as one JSON document."

    def results(self, flights: list[Flight], probe: Callable[[probes.Selection, str], dict[str, Any]]) -> list[Row]:
        from fpv_tools.blackbox import expr, probes

        parts = [probes.Part(f, f.between(self.start, self.end) & expr.mask(f, self.when)) for f in flights]
        groups = [("all flights", parts)] if self.pool else [(p.flight.name, [p]) for p in parts]
        return [{"flights": label, "signal": name, **probe(sel, name)} for label, sel in groups for name in self.signal]

    def report(self, flights: list[Flight], results: list[Row]) -> None:
        if self.json:
            write_json(finite(results))
            return
        names = _short_names(flights)
        if len({f.source for f in flights}) == 1:
            print(f"# {flights[0].source}")
        for result in results:
            _print_result(_shorten(result, names))


def _numbers(text: str | None, option: str) -> list[float]:
    try:
        return [float(x) for x in (text or "").split(",") if x.strip()]
    except ValueError:
        raise ValueError(f"{option} expects comma-separated numbers, got {text!r}") from None


def _band(text: str, option: str = "--band") -> tuple[float, float | None]:
    lo, _, hi = text.partition("-")
    try:
        return float(lo or 0), float(hi) if hi else None
    except ValueError:
        raise ValueError(f"{option} expects ranges like 80- or 10-100, got {text!r}") from None


@app.command
def stats(
    *logs: Log,
    probe: Probe,
    percentiles: Annotated[str, Parameter(metavar="P,...")] = "1,5,50,95,99",
    below: Annotated[str | None, Parameter(metavar="V,...")] = None,
    above: Annotated[str | None, Parameter(metavar="V,...")] = None,
    options: LogOptions = LogOptions(),
) -> None:
    """Distribution of signals: min and max with when they occur, mean, std, percentiles, time beyond thresholds.

    Parameters
    ----------
    percentiles
        Percentiles to report, comma-separated.
    below
        Also report the seconds below these values, comma-separated.
    above
        Also report the seconds above these values, comma-separated.
    """
    from fpv_tools.blackbox import probes

    ps, lows, highs = _numbers(percentiles, "--percentiles"), _numbers(below, "--below"), _numbers(above, "--above")

    def probe_one(sel: probes.Selection, name: str) -> Row:
        result: Row = {"distribution": probes.distribution(sel, name, ps)}
        if lows or highs:
            result["seconds_beyond"] = probes.time_beyond(sel, name, lows, highs)
        return result

    flights = _flights(logs, options)
    probe.report(flights, probe.results(flights, probe_one))


@app.command
def runs(
    *logs: Log,
    probe: Probe,
    min_duration: Annotated[float, Parameter(metavar="SECONDS")] = 0.0,
    options: LogOptions = LogOptions(),
) -> None:
    """Periods where the selection holds (--when, --from/--to), with each signal's range in each.

    E.g. `-s acc --when 'acc > 3'` lists the stretches of high acceleration.

    Parameters
    ----------
    min_duration
        Drop periods shorter than this (s).
    """
    from fpv_tools.blackbox import probes

    flights = _flights(logs, options)
    probe.report(flights, probe.results(flights, lambda sel, name: {"runs": probes.runs(sel, name, min_duration)}))


@app.command
def changes(*logs: Log, probe: Probe, options: LogOptions = LogOptions()) -> None:
    """Every change of value, for discrete signals: mode, failsafe, rx, box.* and the like."""
    from fpv_tools.blackbox import probes

    flights = _flights(logs, options)
    probe.report(flights, probe.results(flights, lambda sel, name: {"changes": probes.changes(sel, name)}))


@app.command
def spectrum(
    *logs: Log,
    probe: Probe,
    peaks: Annotated[int, Parameter(metavar="N")] = 3,
    min_hz: Annotated[float, Parameter(metavar="HZ")] = 20.0,
    separation: Annotated[float, Parameter(metavar="HZ")] = 30.0,
    band: Annotated[str | None, Parameter(metavar="LO-HI,...")] = None,
    nperseg: Annotated[int, Parameter(metavar="N")] = 256,
    vs_throttle: Annotated[float | None, Parameter(metavar="WIDTH")] = None,
    options: LogOptions = LogOptions(),
) -> None:
    """Spectrum of signals (Welch): the strongest peaks, and RMS in frequency bands.

    Parameters
    ----------
    peaks
        Number of peaks to report.
    min_hz
        Ignore peaks below this frequency (Hz).
    separation
        Minimum distance between peaks (Hz).
    band
        Also report the RMS within these bands (Hz), comma-separated, e.g. 80- or 10-100,100-.
    nperseg
        Segment length (samples): the frequency resolution is the log rate divided by this. Stretches of the
        selection shorter than this are left out.
    vs_throttle
        Also the spectrum per throttle band of this width (%, e.g. 10): time spent, strongest peak and RMS above
        --min-hz.
    """
    from fpv_tools.blackbox import probes

    bands = [_band(b) for b in (band or "").split(",") if b.strip()]

    def probe_one(sel: probes.Selection, name: str) -> Row:
        result: Row = {
            "spectrum": probes.spectrum(
                sel, name, peaks=peaks, min_hz=min_hz, separation=separation, bands=bands, nperseg=nperseg
            )
        }
        if vs_throttle is not None:
            result["vs_throttle"] = probes.vs_throttle(sel, name, step=vs_throttle, min_hz=min_hz, nperseg=nperseg)
        return result

    flights = _flights(logs, options)
    probe.report(flights, probe.results(flights, probe_one))


@app.command
def step(
    *logs: Log,
    probe: Probe,
    reference: Annotated[str, Parameter(alias="-r", metavar="NAME")],
    window: Annotated[float, Parameter(metavar="SECONDS")] = 1.0,
    min_amplitude: Annotated[float, Parameter(metavar="VALUE")] = 20.0,
    settle: Annotated[str, Parameter(metavar="FROM-TO")] = "0.15-0.3",
    split: Annotated[float | None, Parameter(metavar="VALUE")] = None,
    curve: bool = False,
    options: LogOptions = LogOptions(),
) -> None:
    """Step response of signals to a reference: steady-state gain, and rise times and peak relative to it.

    E.g. `-s gyro.roll -r setpoint.roll --when 'thr > 5'`: how gyro follows setpoint while in the air.

    Parameters
    ----------
    reference
        The signal the probed signals respond to.
    window
        Length of the windows the response is estimated over (s). Windows where the selection does not hold throughout
        are left out.
    min_amplitude
        Leave out windows where the reference swings less than this (in its unit, e.g. deg/s).
    settle
        Time after the step (s) whose median response is the steady state. The response is estimated up to its end.
    split
        Report windows with a reference swing up to and above this separately (small vs large moves).
    curve
        Print the median response curves as CSV instead (ms, one column per signal and group), per unit of
        reference step: 1 means following exactly.
    """
    from fpv_tools.blackbox import probes

    if curve and probe.json:
        raise ValueError("--curve and --json exclude each other")
    settle_from, settle_to = _band(settle, "--settle")
    if settle_to is None:
        raise ValueError(f"--settle expects a range like 0.15-0.3, got {settle!r}")
    flights = _flights(logs, options)
    results = probe.results(
        flights,
        lambda sel, name: {
            "step_response": probes.step_response(
                sel,
                name,
                reference,
                window=window,
                min_amplitude=min_amplitude,
                settle=(settle_from, settle_to),
                split=split,
            )
        },
    )
    if curve:
        _print_curves(results)
    else:
        probe.report(flights, results)


def _print_curves(results: list[dict[str, Any]]) -> None:
    curves = {
        f"{r['flights']} {r['signal']} {g['reference_swing']}": g["curve"]
        for r in results
        for g in r.get("step_response", [])
        if "curve" in g
    }
    if not curves:
        raise ValueError("no step response curves (too few windows with enough reference swing)")
    length = max(len(c) for c in curves.values())
    rows = ({"ms": i, **{k: c[i] if i < len(c) else None for k, c in curves.items()}} for i in range(length))
    write_csv(rows, ["ms", *curves])


def _print_result(result: dict[str, Any]) -> None:
    print(f"== {result['signal']}  ({result['flights']})")
    if dist := result.get("distribution"):
        if not dist.get("samples"):
            print("   no samples selected\n")
            return
        pct = "  ".join(f"p{k} {number(v)}" for k, v in dist["percentiles"].items())
        print(
            f"   {dist['samples']} samples, {number(dist['seconds'])} s: min {number(dist['min'])} at "
            f"{dist['min_at']['flight']} {dist['min_at']['t']:.2f}s, max {number(dist['max'])} at "
            f"{dist['max_at']['flight']} {dist['max_at']['t']:.2f}s, "
            f"mean {number(dist['mean'])}, std {number(dist['std'])}"
        )
        print(f"   {pct}")
    if beyond := result.get("seconds_beyond"):
        print("   seconds " + ", ".join(f"{k}: {number(v)}" for k, v in beyond.items()))
    for key in ("runs", "changes"):
        if key in result:
            print(f"   {key}:" if result[key] else f"   {key}: none")
            table(result[key])
    if spec := result.get("spectrum"):
        if not spec.get("peaks") and not spec.get("bands"):
            print("   spectrum: no stretch long enough")
        else:
            print(
                f"   spectrum over {number(spec['seconds'])} s (resolution {number(spec['resolution_hz'])} Hz): peaks "
                + ", ".join(f"{p['hz']:.0f} Hz ({number(p['rms'])})" for p in spec["peaks"])
            )
            for b in spec["bands"]:
                print(
                    f"   rms {b['from_hz']:g}-{format(b['to_hz'], 'g') if b['to_hz'] is not None else 'Nyquist'} Hz: "
                    f"{number(b['rms'])}"
                )
    if "vs_throttle" in result:
        print("   spectrum by throttle:")
        table(result["vs_throttle"])
    if "step_response" in result:
        print("   step response:")
        table([{k: v for k, v in g.items() if k != "curve"} for g in result["step_response"]])
    print()


# --- export ----------------------------------------------------------------------------------------------------


@app.command
def export(
    *logs: Log,
    name: Annotated[tuple[str, ...], Parameter(name=["--signal", "-s"], metavar="NAME")] = (),
    when: Annotated[str | None, Parameter(metavar="EXPR")] = None,
    start: Annotated[Seconds, Parameter(name="--from")] = None,
    end: Annotated[Seconds, Parameter(name="--to")] = None,
    every: Seconds = None,
    output: Annotated[str, Parameter(alias="-o", allow_leading_hyphen=True, metavar="FILE")] = "-",
    options: LogOptions = LogOptions(),
) -> None:
    """Signals as CSV: flight, t, then one column per signal.

    Parameters
    ----------
    name
        Signals to export; repeatable. Default: all that `bb info --signals` lists.
    when
        Only rows where this condition holds (`fpv-tools bb --help` has the syntax).
    start
        Only from this time (s since the flight's first frame).
    end
        Only up to this time.
    every
        At most one row per this many seconds (rows are picked, not averaged).
    output
        File to write, or - for stdout.
    """
    import numpy as np

    from fpv_tools.blackbox import expr

    flights = _flights(logs, options)
    names = list(name) or [n for n in flights[0].signal_names() if n != "t"]

    def rows() -> Any:
        for f in flights:
            idx = np.flatnonzero(f.between(start, end) & expr.mask(f, when))
            if every:
                idx = idx[np.unique(np.floor(f.t[idx] / every), return_index=True)[1]]
            columns = {n: f[n] for n in names}
            for i in idx:
                yield {"flight": f.name, "t": round(float(f.t[i]), 6), **{n: _cell(columns[n][i]) for n in names}}

    if output == "-":
        write_csv(rows(), ["flight", "t", *names])
    else:
        with Path(output).open("w", newline="") as out:
            write_csv(rows(), ["flight", "t", *names], out)


def _cell(value: Any) -> Any:
    value = value.item() if hasattr(value, "item") else value
    return round(value, 6) if isinstance(value, float) else value
