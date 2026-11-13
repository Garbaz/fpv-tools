"""MSP v2 transport.

Frame (native v2): ``$X<`` request / ``$X>`` reply / ``$X!`` error, then flag (u8), command (u16),
payload length (u16), payload, and CRC-8/DVB-S2 over flag..payload (msp/msp_serial.c).
"""

import struct
from typing import Protocol, Self

from fpv_tools.fc.msp_codes import CODES, NAMES


class MspError(Exception):
    pass


class MspTimeout(MspError):
    """No (complete) reply in time."""


class Stream(Protocol):
    def read(self, size: int = 1, /) -> bytes: ...
    def write(self, data: bytes, /) -> int | None: ...


def crc8_dvb_s2(data: bytes, crc: int = 0) -> int:
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def encode_frame(code: int, payload: bytes = b"", direction: bytes = b"<") -> bytes:
    body = struct.pack("<BHH", 0, code, len(payload)) + payload
    return b"$X" + direction + body + bytes([crc8_dvb_s2(body)])


def code_of(message: str) -> int:
    """Message code from a firmware name (MSP_RC, MSP2_SET_TEXT) or a number (105, 0x3007)."""
    if message in CODES:
        return CODES[message]
    try:
        return int(message, 0)
    except ValueError:
        raise MspError(f"unknown MSP message {message!r}") from None


def name_of(code: int) -> str:
    return NAMES.get(code, f"0x{code:04x}")


CLI_ECHO = (
    "the flight controller echoed the request back instead of answering: it is in its CLI (left there by another "
    "program or a cut-off session), where MSP goes unheard; `fpv-tools cli version` leaves it, rebooting the FC"
)


def read_frame(stream: Stream, expected: int, max_skip: int = 4096) -> bytes:
    """Read frames until the reply to `expected` arrives; return its payload."""
    skipped = 0
    while skipped < max_skip:
        if _read_exact(stream, 1) != b"$":
            skipped += 1
            continue
        if _read_exact(stream, 1) != b"X":
            continue
        direction = _read_exact(stream, 1)
        try:
            header = _read_exact(stream, 5)
            _flag, code, size = struct.unpack("<BHH", header)
            payload = _read_exact(stream, size)
            (crc,) = _read_exact(stream, 1)
            if crc != crc8_dvb_s2(header + payload):
                raise MspError(f"CRC mismatch in reply to {name_of(code)}")
        except MspError as e:
            # The FC never sends '<'. A request coming back broken is the CLI echoing what it reads as typed
            # text: printable bytes only (cli.c cliProcess)
            if direction == b"<":
                raise MspError(CLI_ECHO) from e
            raise
        if code != expected or direction not in (b">", b"!"):
            continue  # stale reply or echoed request
        if direction == b"!":
            raise MspError(f"flight controller rejected {name_of(code)}")
        return payload
    raise MspError(f"no MSP frame found while waiting for {name_of(expected)}")


def _read_exact(stream: Stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise MspTimeout("timed out waiting for the flight controller")
    return data


class MspClient:
    def __init__(self, stream: Stream) -> None:
        self.stream = stream

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        if close := getattr(self.stream, "close", None):
            close()

    def request(self, code: int, payload: bytes = b"") -> bytes:
        if reset := getattr(self.stream, "reset_input_buffer", None):
            reset()
        self.stream.write(encode_frame(code, payload))
        return read_frame(self.stream, code)
