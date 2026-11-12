"""Typed access to blackbox log headers (the `H name:value` lines at the start of each log)."""

from collections.abc import Iterator, Mapping


class MissingHeader(KeyError):
    def __str__(self) -> str:
        return f"log header has no {self.args[0]!r}; this log cannot provide that value"


class Headers(Mapping[str, str]):
    def __init__(self, raw: Mapping[str, str]) -> None:
        self.raw = dict(raw)

    def __getitem__(self, key: str) -> str:
        try:
            return self.raw[key]
        except KeyError:
            raise MissingHeader(key) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self.raw)

    def __len__(self) -> int:
        return len(self.raw)

    def number(self, key: str) -> int:
        return int(self[key], 0)

    def numbers(self, key: str) -> tuple[int, ...]:
        return tuple(int(v, 0) for v in self[key].split(","))

    @property
    def firmware(self) -> str:
        return self.get("Firmware revision", "?")

    @property
    def craft_name(self) -> str:
        return self.get("Craft name", "")

    @property
    def motor_output(self) -> tuple[int, int]:
        """Motor output range (low, high) as logged in motor[n]."""
        lo, hi = self.numbers("motorOutput")
        return lo, hi

    @property
    def acc_1g(self) -> int:
        return self.number("acc_1G")

    @property
    def cell_voltages(self) -> tuple[float, float, float]:
        """(min, warning, max) per-cell voltage in V: vbatmin/warning/maxcellvoltage."""
        vmin, vwarn, vmax = self.numbers("vbatcellvoltage")
        return vmin / 100, vwarn / 100, vmax / 100

    @property
    def current_sensor(self) -> tuple[int, int]:
        """(offset in mA, scale) of the ADC current sensor: A = mV * 10 / scale + offset / 1000 (sensors/current.c)."""
        offset, scale = self.numbers("currentSensor")
        return offset, scale
