"""Writing results: aligned text tables for people, CSV for data, JSON for scripts. Data goes to stdout."""

import csv
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TextIO


def number(value: Any) -> str:
    """A value for a text table: floats to sensible precision, None as blank."""
    match value:
        case None:
            return ""
        case bool():
            return "yes" if value else "no"
        case float() if math.isnan(value):
            return ""
        case float():
            size = abs(value)
            if size >= 100 or size == 0:
                return f"{value:.0f}"
            return f"{value:.1f}" if size >= 10 else f"{value:.2f}" if size >= 0.1 else f"{value:.3g}"
        case _:
            return str(value)


def table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None, out: TextIO = sys.stdout) -> None:
    """Rows as an aligned text table; columns default to the union of keys, in first-seen order."""
    if not rows:
        return
    columns = list(columns or dict.fromkeys(k for row in rows for k in row))
    cells = [list(columns)] + [[number(row.get(c)) for c in columns] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(columns))]
    for n, r in enumerate(cells):
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip(), file=out)
        if n == 0:
            print("  ".join("-" * w for w in widths), file=out)


def write_csv(rows: Iterable[Mapping[str, Any]], columns: Sequence[str], out: TextIO = sys.stdout) -> None:
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(c)) for c in columns])


def _csv_cell(value: Any) -> Any:
    """Missing and unknown values (None, NaN) as empty cells, as CSV readers expect."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return value


def write_json(value: Any, out: TextIO = sys.stdout, *, line: bool = False) -> None:
    """One JSON document, or one compact line (JSONL) with line=True."""
    json.dump(value, out, indent=None if line else 2, default=_default, allow_nan=False)
    out.write("\n")
    out.flush()


def _default(value: Any) -> Any:
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"cannot write {type(value).__name__} as JSON")


def finite(value: Any) -> Any:
    """NaN/inf to None, recursively, for JSON output."""
    match value:
        case float() if not math.isfinite(value):
            return None
        case dict():
            return {k: finite(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
        case list() | tuple():
            return [finite(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
        case _:
            return value
