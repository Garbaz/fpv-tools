"""Loading flights from blackbox files: decoding (bbl.py), caching decoded logs, selecting flights."""

import contextlib
import hashlib
import json
import os
import tempfile
import warnings
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

from fpv_tools.blackbox import bbl
from fpv_tools.blackbox.flight import Flight
from fpv_tools.settings import Settings

# Bump when decoding changes what ends up in the cache
DECODER_VERSION = "1"
LOG_SUFFIXES = (".bbl", ".bfl", ".txt")
# The whole flash as one file, next to the single logs, in the FC's USB mass storage mode (msc/emfat_file.c:
# FC_FIRMWARE_IDENTIFIER "_ALL.BBL")
WHOLE_FLASH = "BTFL_ALL.BBL"


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "fpv-tools" / "logs"


def decode_file(path: Path) -> list[bbl.Log]:
    """Every log in a file, decoded once and then read from the cache."""
    data = path.read_bytes()
    key = hashlib.sha256(data + DECODER_VERSION.encode()).hexdigest()[:24]
    directory = cache_root() / key
    if (directory / "logs.json").exists():
        return _read_cache(directory)
    logs = [bbl.decode(raw) for raw in bbl.split(data)]
    _write_cache(directory, logs)
    return logs


def _write_cache(directory: Path, logs: list[bbl.Log]) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=directory.parent))
    meta: list[dict[str, Any]] = []
    for i, log in enumerate(logs):
        arrays = {f"main/{k}": v for k, v in log.main.items()}
        arrays |= {f"slow/{k}": v for k, v in log.slow.items()}
        arrays |= {f"gps/{k}": v for k, v in log.gps.items()}
        np.savez(staging / f"{i}.npz", allow_pickle=False, **arrays)
        meta.append({"headers": log.headers, "events": log.events, "gps_home": log.gps_home, "stats": log.stats})
    (staging / "logs.json").write_text(json.dumps(meta))
    with contextlib.suppress(OSError):  # another process may have cached it first
        staging.rename(directory)  # atomic: a half-written entry never looks complete


def _read_cache(directory: Path) -> list[bbl.Log]:
    logs: list[bbl.Log] = []
    for i, meta in enumerate(json.loads((directory / "logs.json").read_text())):
        groups: dict[str, dict[str, Any]] = {"main": {}, "slow": {}, "gps": {}}
        with np.load(directory / f"{i}.npz", allow_pickle=False) as arrays:
            for name in arrays.files:
                group, _, field = name.partition("/")
                groups[group][field] = arrays[name]
        logs.append(
            bbl.Log(
                meta["headers"],
                groups["main"],
                groups["slow"],
                groups["gps"],
                meta["gps_home"],
                meta["events"],
                meta["stats"],
            )
        )
    return logs


def find_logs(paths: Iterable[str | Path]) -> Iterator[tuple[Path, set[int] | None]]:
    """Expand log arguments: FILE, FILE:N, FILE:N-M or a directory (its logs, without the whole-flash file)."""
    for arg in paths:
        text = str(arg)
        selection: set[int] | None = None
        path = Path(text)
        if not path.exists() and ":" in text:
            text, _, spec = text.rpartition(":")
            path = Path(text)
            lo, _, hi = spec.partition("-")
            selection = set(range(int(lo), int(hi or lo) + 1))
        if path.is_dir():
            for log in sorted(p for p in path.iterdir() if p.suffix.lower() in LOG_SUFFIXES):
                if log.name.upper() != WHOLE_FLASH:
                    yield log, None
        elif path.exists():
            yield path, selection
        else:
            raise bbl.FormatError(f"{arg}: no such log")


def load(
    paths: Iterable[str | Path],
    *,
    min_frames: int = 2,
    settings: Settings | None = None,
    ignore_version: bool = False,
) -> list[Flight]:
    """Flights from the given logs, in argument order. A flight present twice (e.g. in a single-flight file and in
    a full flash dump) is kept once, recognised by device, first FC timestamp and length. Both that and logs too
    short to be a time series are reported as warnings."""
    flights: list[Flight] = []
    seen: dict[tuple[str | None, int, int], str] = {}
    for path, selection in find_logs(paths):
        for index, log in enumerate(decode_file(path), 1):
            if selection is not None and index not in selection:
                continue
            here = f"{path}:{index}"
            frames = len(log.main.get("time", ()))
            if frames < min_frames:
                warnings.warn(f"{here}: left out, {frames} frame(s)", stacklevel=2)
                continue
            key = (log.headers.get("DeviceUID"), int(log.main["time"][0]), frames)
            if key in seen:
                warnings.warn(f"{here}: the same flight as {seen[key]}, kept once", stacklevel=2)
                continue
            seen[key] = here
            flights.append(Flight(log, source=path, index=index, settings=settings, ignore_version=ignore_version))
    return flights


DecodeError = bbl.FormatError
