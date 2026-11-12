"""Stand-ins for a flight controller on the other end of the serial port, and synthetic flights."""

import struct
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from fpv_tools.blackbox.bbl import Log
from fpv_tools.blackbox.flight import Flight
from fpv_tools.fc.msp import encode_frame
from fpv_tools.settings import Settings


class FakeMsp:
    """Answers MSP requests through `handler(code, payload) -> reply payload | None` (None = error frame)."""

    def __init__(self, handler: Callable[[int, bytes], bytes | None]) -> None:
        self.handler = handler
        self.buffer = bytearray()
        self.requests: list[tuple[int, bytes]] = []
        self.port = None  # not a USB port: no waiting for reboots

    def write(self, data: bytes, /) -> int:
        _flag, code, size = struct.unpack_from("<BHH", data, 3)
        payload = bytes(data[8 : 8 + size])
        self.requests.append((code, payload))
        reply = self.handler(code, payload)
        self.buffer += encode_frame(code, direction=b"!") if reply is None else encode_frame(code, reply, b">")
        return len(data)

    def read(self, size: int = 1, /) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


class FakeCli:
    """Betaflight's CLI as seen over USB (cli/cli.c): '#' enters it from MSP mode with a banner and prompt; inside
    the CLI, '#' is only echoed (a comment line). Commands are echoed and answered from `answers`, then the prompt.
    `save` and `exit` leave the CLI as the firmware does; the reboot drops the port."""

    def __init__(self, answers: Mapping[str, str], in_cli: bool = False) -> None:
        self.answers = answers
        self.in_cli = in_cli
        self.rebooted = False
        self.buffer = bytearray()
        self.written: list[str] = []
        self.timeout = 1.0
        self.port = None

    def write(self, data: bytes, /) -> int:
        text = data.decode()
        self.written.append(text)
        command = text.strip()
        if text == "#" and not self.in_cli:
            self.in_cli = True
            self.buffer += b"\r\nEntering CLI Mode, type 'exit' to reboot, or 'help'\r\n\r\n# "
        elif text == "#":
            self.buffer += b"#"
        elif text == "\r\n":
            self.buffer += b"\r\n# "
        elif command in ("save", "exit"):  # cliSave, cliExitCmd
            line = {"save": "saving", "exit": "leaving CLI mode, unsaved changes lost"}[command]
            self.buffer += f"{command}\r\n\r\n# {line}\r\n\r\nRebooting".encode()
            self.rebooted = True
        elif command == "exit noreboot":
            self.buffer += b"exit noreboot\r\n\r\n# leaving CLI mode, no reboot\r\n"
            self.in_cli = False
        elif command == "save noreboot":
            self.buffer += b"save noreboot\r\n\r\n# saving\r\n\r\n# "
        else:
            self.buffer += f"{command}\r\n{self.answers.get(command, '')}\r\n# ".encode()
        return len(data)

    def read(self, size: int = 1, /) -> bytes:
        if self.rebooted and not self.buffer:
            raise OSError("device disconnected")
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


HEADERS = {
    "Firmware revision": "Betaflight 2026.6.2 (0db924466) STM32G47X",
    "motorOutput": "158,2047",
    "acc_1G": "2048",
    "vbatcellvoltage": "345,360,440",
    "vbatref": "420",
    "currentSensor": "0,570",
    "serialrx_provider": "9",
    "features": "268698632",
    "debug_mode": "59",
    "vbat_sag_compensation": "0",
}


SLOW_FIELDS = ("flightModeFlags", "stateFlags", "failsafePhase", "rxSignalReceived", "rxFlightChannelsValid")


def flight(
    columns: Mapping[str, Any],
    *,
    rate: float = 1000.0,
    headers: Mapping[str, str] | None = None,
    settings: Mapping[str, str] | None = None,
    **more_settings: str,
) -> Flight:
    """A synthetic flight: columns are raw log fields (arrays or scalars) as the decoder produces them, sampled at
    `rate` Hz; slow-frame fields go where the decoder puts them. Settings as the FC would have them."""
    length = max((len(v) for v in columns.values() if np.ndim(v)), default=10)
    main: dict[str, Any] = {"time": np.arange(length, dtype=np.int64) * int(1e6 / rate) + 5_000_000}
    slow: dict[str, Any] = {}
    for name, value in columns.items():
        array = np.broadcast_to(value, (length,))
        if name in SLOW_FIELDS:
            slow[name] = array.astype(float)
        else:
            main[name] = array.astype(np.int64)
    log = Log({**HEADERS, **(headers or {})}, main, slow, {}, [], [], {})
    return Flight(log, settings=Settings({**(settings or {}), **more_settings}))
