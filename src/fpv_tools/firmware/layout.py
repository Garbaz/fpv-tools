"""The shape of one MSP message's payloads, and what their values mean.

A layout's structs are the bytes as the firmware serialises them: their values are what `fpv-tools msp --raw`
shows. Its meanings translate those wire values to what the firmware means by them (names, units) and back, by
rules from the firmware source. Translations are lossless: input without an exact wire value is an error, and a
value without a name stays a number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import construct as c

type Value = int | float | str | bool | list[Value] | dict[str, Value] | None  # plain values, as printed


class MeaningError(ValueError):
    pass


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
    """A quantity: wire * scale + offset, in unit."""

    scale: Decimal
    unit: str
    offset: int = 0

    def decode(self, value: int) -> Value:
        quantity = value * self.scale + self.offset
        return int(quantity) if quantity == quantity.to_integral_value() else float(quantity)

    def encode(self, text: str) -> int:
        try:
            wire = (Decimal(text) - self.offset) / self.scale
        except InvalidOperation:
            raise MeaningError(f"{text!r} is not a number of {self.unit}") from None
        if wire != wire.to_integral_value():
            step = f"{self.scale} {self.unit}" + (f" from {self.offset}" if self.offset else "")
            raise MeaningError(f"{text} {self.unit} is not a whole number of steps of {step}")
        return int(wire)


def scaled(scale: str, unit: str, offset: int = 0) -> Linear:
    return Linear(Decimal(scale), unit, offset)


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
    """A list whose positions have names (channel order), printed as NAME=value. Positions past the names keep
    their index."""

    names: Sequence[str]

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

    def units(self) -> dict[str, str]:
        return {name: m.unit for name, m in self.meaning.items() if isinstance(m, Linear)}
