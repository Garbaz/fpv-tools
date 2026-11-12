"""MSP payloads: named layouts as `construct` structs, ad-hoc ones as `struct` format strings.

Layouts live in the firmware profile (they change between releases). This module converts between payload bytes,
plain Python values (for printing and JSON), and the text the command line provides.
"""

import struct
from collections.abc import Mapping, Sequence
from typing import Any, cast

import construct as c

type Plain = int | float | str | bool | list[Plain] | dict[str, Plain] | None


class CodecError(Exception):
    pass


def plain(value: Any) -> Plain:
    """construct's parse result as plain values: enums by name, bytes as hex, no private keys."""
    match value:
        case c.EnumIntegerString():
            return str(value)
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


def parse(layout: c.Struct, data: bytes) -> dict[str, Plain]:
    try:
        result = plain(layout.parse(data))
    except c.ConstructError as e:
        raise CodecError(f"reply does not match the known layout: {e}") from e
    return result if isinstance(result, dict) else {"value": result}


def build(layout: c.Struct, args: Mapping[str, str]) -> bytes:
    """Payload from NAME=VALUE arguments. Trailing fields may be left out (MSP requests often have optional tails)."""
    fields = [sub for sub in layout.subcons if isinstance(sub, c.Renamed) and sub.name]
    names = [str(sub.name) for sub in fields]
    unknown = set(args) - set(names)
    if unknown:
        raise CodecError(f"unknown fields {sorted(unknown)}; this message takes {names}")
    given = [sub for sub in fields if sub.name in args]
    if given != fields[: len(given)]:
        raise CodecError(f"fields must be given in order without gaps: {names}")
    try:
        return b"".join(sub.build(_coerce(sub.subcon, args[str(sub.name)])) for sub in given)
    except (c.ConstructError, ValueError) as e:
        raise CodecError(str(e)) from e


def _coerce(subcon: c.Construct[Any, Any], text: str) -> Any:
    """Command-line text to the value a construct field expects (numbers, enum names, text, hex, lists)."""
    if subcon is c.GreedyBytes:
        return bytes.fromhex(text)
    match subcon:
        case c.Enum():
            return int(text, 0) if text.lstrip("-").isdigit() else text
        case c.FormatField():
            fmt: str = subcon.fmtstr  # pyright: ignore[reportUnknownMemberType]
            return float(text) if fmt[-1] in "fd" else int(text, 0)
        case c.StringEncoded():
            return text
        case c.GreedyRange() | c.Array():
            inner = cast(c.Construct[Any, Any], subcon.subcon)  # pyright: ignore[reportUnknownMemberType]
            return [_coerce(inner, item) for item in text.split(",") if item]
        case c.Subconstruct():
            return _coerce(cast(c.Construct[Any, Any], subcon.subcon), text)  # pyright: ignore[reportUnknownMemberType]
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
