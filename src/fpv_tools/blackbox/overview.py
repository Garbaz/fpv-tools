"""What a log contains, without looking into the time series: firmware, craft, flights, fields, events, headers."""

from collections.abc import Sequence
from typing import Any

from fpv_tools.blackbox.flight import Flight

type Row = dict[str, Any]

# Header keys that describe the log and craft rather than settings
IDENTITY = ("Firmware revision", "Firmware date", "Board information", "Craft name", "DeviceUID", "Log start datetime")


def power_ups(flights: Sequence[Flight]) -> list[int]:
    """Number flights by power-up: consecutive flights from one log file and FC whose FC time keeps increasing
    belong to one power-up (one battery). A reboot restarts FC time; separate files always start a new one."""
    out: list[int] = []
    number, prev = 0, None
    for f in flights:
        same = (
            prev is not None
            and prev.source == f.source
            and prev.headers.get("DeviceUID") == f.headers.get("DeviceUID")
            and f.t_us[0] > prev.t_us[-1]
        )
        number += 0 if same else 1
        out.append(number)
        prev = f
    return out


def describe(flights: Sequence[Flight]) -> list[Row]:
    """One entry per log file: identity, and per flight its timing, logged fields, debug mode and log events.
    Headers are split into those all flights of the file share and those that differ."""
    files: dict[str, list[tuple[Flight, int]]] = {}
    for f, power_up in zip(flights, power_ups(flights), strict=True):
        files.setdefault(str(f.source), []).append((f, power_up))
    out: list[Row] = []
    for path, members in files.items():
        first = members[0][0]
        keys = list(dict.fromkeys(k for f, _ in members for k in f.headers if not k.startswith("Field ")))
        shared = {k: first.headers.get(k) for k in keys if len({f.headers.get(k) for f, _ in members}) == 1}
        out.append(
            {
                "file": path,
                "firmware": first.headers.firmware,
                "board": first.headers.get("Board information"),
                "craft": first.headers.craft_name,
                "device_uid": first.headers.get("DeviceUID"),
                "flights": [_flight(f, power_up, keys, shared) for f, power_up in members],
                "headers": {k: v for k, v in shared.items() if k not in IDENTITY},
            }
        )
    return out


def _flight(f: Flight, power_up: int, keys: Sequence[str], shared: dict[str, Any]) -> Row:
    debug_index = f.headers.get("debug_mode")
    debug_modes = f.profile.debug_modes
    return {
        "index": f.index,
        "power_up": power_up,
        "fc_time_s": float(f.t_us[0] / 1e6),
        "duration_s": f.duration,
        "frames": len(f),
        "rate_hz": f.fs,
        "debug_mode": debug_modes[int(debug_index)]
        if debug_index is not None and int(debug_index) < len(debug_modes)
        else debug_index,
        "fields": list(f.fields),
        "decoding": f.log.stats,
        "events": [_event(f, e) for e in f.log_events],
        "headers": {k: f.headers.get(k) for k in keys if k not in shared},
    }


def _event(f: Flight, event: dict[str, Any]) -> Row:
    """A log event with time relative to the flight's first frame and its values named after the firmware."""
    p = f.profile
    t = (event["time"] - int(f.t_us[0])) / 1e6 if event.get("time") is not None else None
    row: Row = {"t": t, "event": event["event"]}
    match event["event"]:
        case "disarm":
            reason = event["reason"]
            row["reason"] = p.disarm_reasons[reason] if reason < len(p.disarm_reasons) else reason
        case "flight_mode":
            now, before = event["flags"], event["last_flags"]
            changed = [b for i, b in enumerate(p.box_ids) if (now ^ before) >> i & 1]
            row["changes"] = " ".join(f"{'+' if now >> p.box_ids.index(b) & 1 else '-'}{b}" for b in changed)
        case "inflight_adjustment":
            function = event["function"]
            names = p.adjustment_functions
            row["function"] = names[function] if function < len(names) else function
            row["value"] = event["value"]
        case "logging_resume":
            row["iteration"] = event["iteration"]
        case _:
            pass
    return row
