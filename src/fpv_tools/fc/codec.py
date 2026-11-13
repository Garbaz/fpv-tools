"""MSP payloads: named layouts as `construct` structs, ad-hoc ones as `struct` format strings.

Layouts live in the firmware profile (they change between releases). This module converts between payload bytes,
plain Python values (for printing and JSON), and the text the command line provides; with a layout's meanings,
values are what the firmware means by them (names, units) rather than as sent.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from typing import Any, cast

import construct as c

from fpv_tools.firmware.layout import Flags, Linear, Meaning, MeaningError, Names, Positions
from fpv_tools.firmware.layout import Value as Plain


class CodecError(Exception):
    pass


def plain(value: Any) -> Plain:
    """construct's parse result as plain values: bytes as hex, no private keys."""
    match value:
        case dict():
            items: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
            return {str(k): plain(v) for k, v in items.items() if not str(k).startswith("_")}
        case list():
            entries: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
            return [plain(v) for v in entries]
        case bytes():
            return value.hex()
        case int() | float() | str() | bool() | None:
            return value
        case _:
            return str(value)


def parse(layout: c.Struct, data: bytes, meaning: Mapping[str, Meaning] | None = None) -> dict[str, Plain]:
    """Fields of a payload, as sent or (with meaning) as meant."""
    try:
        result = plain(layout.parse(data))
    except c.ConstructError as e:
        raise CodecError(f"reply does not match the known layout: {e}") from e
    result = _meant(result, meaning or {})
    return result if isinstance(result, dict) else {"value": result}


def _meant(value: Plain, meaning: Mapping[str, Meaning]) -> Plain:
    match value:
        case dict():
            return {k: _decode(meaning[k], v) if k in meaning else _meant(v, meaning) for k, v in value.items()}
        case list():
            return [_meant(v, meaning) for v in value]
        case _:
            return value


def _decode(meaning: Meaning, value: Plain) -> Plain:
    match meaning, value:
        case Positions(), list():
            return meaning.decode(cast("list[Any]", value))
        case Positions(), _:
            raise CodecError(f"positions of a value that is not a list: {value!r}")
        case _, list():
            return [_decode(meaning, v) for v in value]
        case _, int():
            return meaning.decode(value)
        case _:
            raise CodecError(f"cannot translate {value!r}")


def build(layout: c.Struct, args: Mapping[str, str], meaning: Mapping[str, Meaning] | None = None) -> bytes:
    """Payload from NAME=VALUE arguments, as sent or (with meaning) as meant. Trailing fields may be left out (MSP
    requests often have optional tails)."""
    fields = [sub for sub in layout.subcons if isinstance(sub, c.Renamed) and sub.name]
    names = [str(sub.name) for sub in fields]
    unknown = set(args) - set(names)
    if unknown:
        raise CodecError(f"unknown fields {sorted(unknown)}; this message takes {names}")
    given = [sub for sub in fields if sub.name in args]
    if given != fields[: len(given)]:
        raise CodecError(f"fields must be given in order without gaps: {names}")
    meaning = meaning or {}
    out = b""
    for sub in given:
        name = str(sub.name)
        text = args[name]
        try:
            value = _encode(meaning[name], sub.subcon, text) if name in meaning else _coerce(sub.subcon, text)
            out += sub.build(value)
        except (MeaningError, CodecError) as e:
            raise CodecError(f"{name}={text}: {e}") from e
        except (c.ConstructError, ValueError) as e:
            raise CodecError(f"{name}={text}: {e}") from e
    return out


def _encode(meaning: Meaning, subcon: c.Construct[Any, Any], text: str) -> Any:
    """Command-line text as meant to the value a construct field expects."""
    match meaning:
        case Positions():
            raise CodecError("printed by position, it cannot be given")
        case Flags():
            return meaning.encode(text)
        case _ if isinstance(subcon, c.GreedyRange | c.Array):
            inner = cast("c.Construct[Any, Any]", subcon.subcon)  # pyright: ignore[reportUnknownMemberType]
            return [_encode(meaning, inner, item) for item in text.split(",") if item]
        case Linear():
            return meaning.encode(text, _bounds(subcon))
        case Names():
            return _fitting(meaning.encode(text), subcon)


def _bounds(subcon: c.Construct[Any, Any]) -> tuple[int, int] | None:
    """The integers a field can hold."""
    if not isinstance(subcon, c.FormatField):
        return None
    fmt: str = subcon.fmtstr  # pyright: ignore[reportUnknownMemberType]
    if fmt[-1] not in "bBhHiIlLqQ":
        return None
    bits = 8 * struct.calcsize(fmt)
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if fmt[-1].islower() else (0, (1 << bits) - 1)


def _fitting(value: int, subcon: c.Construct[Any, Any]) -> int:
    if (bounds := _bounds(subcon)) and not bounds[0] <= value <= bounds[1]:
        raise CodecError(f"outside {bounds[0]}..{bounds[1]}")
    return value


def _coerce(subcon: c.Construct[Any, Any], text: str) -> Any:
    """Command-line text to the value a construct field expects (numbers, text, hex, lists)."""
    if subcon is c.GreedyBytes:
        return bytes.fromhex(text)
    match subcon:
        case c.FormatField():
            fmt: str = subcon.fmtstr  # pyright: ignore[reportUnknownMemberType]
            try:
                return float(text) if fmt[-1] in "fd" else _fitting(int(text, 0), subcon)
            except ValueError:
                raise CodecError("not a number") from None
        case c.StringEncoded():
            return text
        case c.GreedyRange() | c.Array():
            inner = cast("c.Construct[Any, Any]", subcon.subcon)  # pyright: ignore[reportUnknownMemberType]
            return [_coerce(inner, item) for item in text.split(",") if item]
        case c.Subconstruct():
            return _coerce(cast("c.Construct[Any, Any]", subcon.subcon), text)  # pyright: ignore[reportUnknownMemberType]
        case _:
            raise CodecError(f"cannot fill a {type(subcon).__name__} field from the command line")


def unpack(fmt: str, data: bytes) -> list[Plain]:
    """Decode with a `struct` format, repeated while whole records remain (e.g. "<H" reads all u16s)."""
    if not fmt or fmt[0] not in "<>!=@":
        fmt = "<" + fmt  # MSP is little-endian
    try:
        size = struct.calcsize(fmt)
        records = [list(r) for r in struct.iter_unpack(fmt, data[: len(data) - len(data) % size])]
    except struct.error as e:
        raise CodecError(f"format {fmt!r}: {e}") from e
    flat: list[Plain] = [v if not isinstance(v, bytes) else v.hex() for r in records for v in r]
    return flat


def pack(fmt: str, values: Sequence[str]) -> bytes:
    if not fmt or fmt[0] not in "<>!=@":
        fmt = "<" + fmt
    try:
        kinds = struct.unpack(fmt, bytes(struct.calcsize(fmt)))
        typed = [float(v) if isinstance(k, float) else int(v, 0) for k, v in zip(kinds, values, strict=True)]
        return struct.pack(fmt, *typed)
    except (struct.error, ValueError) as e:
        raise CodecError(f"format {fmt!r} with values {list(values)}: {e}") from e
