"""The shape of one MSP message's payloads, and what their values mean.

A layout's structs are the bytes as the firmware serialises them: their values are what `fpv-tools msp --raw`
shows and takes, plain numbers. Its meanings translate those wire values to what the firmware means by them (names,
quantities in units) and back, by rules from the firmware source.

The two forms cannot be mistaken for each other: a quantity is printed with its unit and given with one (a bare
number is refused, see BARE_QUANTITIES), and a number that has a name is given by the name. Translations are
lossless both ways: what is printed is accepted as input and means the same value, and input without an exact
wire value (between steps, outside the firmware's range) is refused, never rounded.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import construct as c

type Value = int | float | str | bool | list[Value] | dict[str, Value] | None  # plain values, as printed

# Whether a quantity given without a unit is taken in the unit it is printed in. While False, a bare number is
# refused outside --raw: a value meant as sent (25 us steps, 0.01 V) cannot pass for a quantity by mistake.
BARE_QUANTITIES = False

PREFIXES = {"p": -12, "n": -9, "u": -6, "µ": -6, "μ": -6, "m": -3, "k": 3, "M": 6, "G": 9}  # µ: both code points
NUMBER = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(\S*)\s*")


class MeaningError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    """A unit as printed: a fixed symbol, chosen for how the firmware and its users talk about the value (1700us,
    5733MHz, 0.7V), never rescaled by size. Input takes the base unit with any SI prefix (1.7ms is 1700us), except
    for units where prefixes would be ambiguous or meaningless (deg; B, kB vs KiB)."""

    symbol: str
    quantity: str  # what it measures, for messages
    base: str
    exponent: int = 0  # of symbol relative to base: us is s at -6
    prefixes: bool = True
    aliases: tuple[str, ...] = ()

    def format(self, value: Value | Decimal) -> str:
        return f"{value}{self.symbol}"

    def parse(self, text: str) -> Decimal:
        """A quantity in this unit, exactly."""
        m = NUMBER.fullmatch(text)
        if not m:
            raise MeaningError(f"{text!r} is not a quantity like 1{self.symbol}")
        number, unit = Decimal(m[1]), m[2]
        if not unit:
            if BARE_QUANTITIES:
                return number
            raise MeaningError(f"{text} needs its unit ({text}{self.symbol}); --raw takes values as sent")
        if unit == self.base or unit in self.aliases:
            exponent = 0
        elif self.prefixes and unit.endswith(self.base) and unit[: -len(self.base)] in PREFIXES:
            exponent = PREFIXES[unit[: -len(self.base)]]
        else:
            raise MeaningError(f"{text}: {unit} is not a unit of {self.quantity}")
        return number.scaleb(exponent - self.exponent)


US = Unit("us", "time", "s", -6)
VOLT = Unit("V", "voltage", "V")
AMPERE = Unit("A", "current", "A")
MAH = Unit("mAh", "charge", "Ah", -3)
DEGREE = Unit("deg", "angle", "deg", prefixes=False, aliases=("°",))
MHZ = Unit("MHz", "frequency", "Hz", 6)
BYTE = Unit("B", "size", "B", prefixes=False)


@dataclass(frozen=True)
class Names:
    """Values by name (an enum, a table). Input takes the printed form: a name, or the number of a value without
    one. A number that has a name is refused, as it could be meant in another numbering (AUX3 is index 2)."""

    names: Mapping[int, str]

    def decode(self, value: int) -> Value:
        return self.names.get(value, value)

    def encode(self, text: str) -> int:
        values = {name: value for value, name in self.names.items()}
        if text in values:
            return values[text]
        try:
            number = int(text, 0)
        except ValueError:
            raise MeaningError(f"{text!r} is none of {', '.join(values)}") from None
        if number in self.names:
            raise MeaningError(f"{number} is {self.names[number]}: give the name")
        return number


def indexed(names: Sequence[str]) -> Names:
    return Names(dict(enumerate(names)))


@dataclass(frozen=True)
class Linear:
    """A quantity: wire * scale + offset, in unit; wire values within the firmware's range where it states one."""

    scale: Decimal
    unit: Unit
    offset: int = 0
    wire_range: tuple[int, int] | None = None

    def decode(self, value: int) -> Value:
        quantity = value * self.scale + self.offset
        return int(quantity) if quantity == quantity.to_integral_value() else float(quantity)

    def encode(self, text: str, bounds: tuple[int, int] | None = None) -> int:
        """The wire value of a quantity; `bounds` are what the field can hold."""
        wire = (self.unit.parse(text) - self.offset) / self.scale
        if wire != wire.to_integral_value():
            step = self.unit.format(self.scale) + (f" from {self.unit.format(self.offset)}" if self.offset else "")
            raise MeaningError(f"{text} is not a whole number of steps of {step}")
        lo, hi = _intersect(self.wire_range, bounds)
        if (lo is not None and wire < lo) or (hi is not None and wire > hi):
            ends = [self.unit.format(self.decode(end)) if end is not None else "" for end in (lo, hi)]
            raise MeaningError(f"{text} is outside {ends[0]}..{ends[1]}")
        return int(wire)


def _intersect(*ranges: tuple[int, int] | None) -> tuple[int | None, int | None]:
    lows, highs = [r[0] for r in ranges if r], [r[1] for r in ranges if r]
    return (max(lows) if lows else None), (min(highs) if highs else None)


def scaled(scale: str, unit: Unit, offset: int = 0, wire_range: tuple[int, int] | None = None) -> Linear:
    return Linear(Decimal(scale), unit, offset, wire_range)


@dataclass(frozen=True)
class Flags:
    """A bit field as the names of its set bits; bits without a name stay a number."""

    bits: Mapping[int, str]  # mask -> name

    def decode(self, value: int) -> Value:
        names: list[Value] = [name for mask, name in self.bits.items() if value & mask]
        rest = value & ~sum(self.bits)
        return [*names, rest] if rest else names

    def encode(self, text: str) -> int:
        masks = {name: mask for mask, name in self.bits.items()}
        out = 0
        for item in filter(None, text.split(",")):
            out |= masks[item] if item in masks else Names({}).encode(item)
        return out


@dataclass(frozen=True)
class Positions:
    """A list whose positions have names (channel order), printed as NAME=value, the values in unit if given.
    Positions past the names keep their index. Output only."""

    names: Sequence[str]
    unit: Unit | None = None

    def decode(self, values: list[Value]) -> Value:
        return {self.names[i] if i < len(self.names) else str(i): v for i, v in enumerate(values)}


type Meaning = Names | Linear | Flags | Positions


@dataclass(frozen=True)
class MspLayout:
    """Payload fields of one MSP message: what a request carries, what the reply contains, and what their values
    mean, by field name (a name means the same in request and reply, at any depth)."""

    request: c.Struct = field(default_factory=c.Struct)
    reply: c.Struct = field(default_factory=c.Struct)
    meaning: Mapping[str, Meaning] = field(default_factory=lambda: dict[str, Meaning]())

    def units(self) -> dict[str, Unit]:
        """The unit of each field that has one (for a list or positions: of each value in it)."""
        return {
            name: m.unit for name, m in self.meaning.items() if isinstance(m, Linear | Positions) and m.unit is not None
        }
