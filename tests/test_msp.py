import contextlib
import io
import struct
import sys
from typing import Any

import construct as c
import pytest

from fakes import FakeMsp
from fpv_tools.app import fc as fc_app
from fpv_tools.fc import codec, dataflash
from fpv_tools.fc.msp import MspClient, MspError, crc8_dvb_s2, encode_frame
from fpv_tools.fc.msp_codes import CODES, INBOUND, NAMES
from fpv_tools.firmware import PROFILES
from fpv_tools.firmware.msp_effects import EFFECTS


def test_crc8_dvb_s2_check_value() -> None:
    assert crc8_dvb_s2(b"123456789") == 0xBC  # CRC catalogue check value


def test_reply_matching_skips_stale_frames_and_rejects_bad_crc() -> None:
    fake = FakeMsp(lambda code, payload: b"\x01\x02")
    fake.buffer += b"noise" + encode_frame(CODES["MSP_ANALOG"], b"\x00", b">")  # left over from an earlier request
    assert MspClient(fake).request(CODES["MSP_RC"]) == b"\x01\x02"
    bad = bytearray(encode_frame(CODES["MSP_RC"], b"\x00\x00", b">"))
    bad[-1] ^= 0xFF
    fake.buffer += bad
    with pytest.raises(MspError, match="CRC"):
        MspClient(fake).request(CODES["MSP_RC"])


def test_dataflash_read_follows_short_replies() -> None:
    # The FC answers at most what fits its MSP buffer (serializeDataflashReadReply), which can be less than asked
    flash = bytes(range(256)) * 40

    def fc(code: int, payload: bytes) -> bytes:
        if code == CODES["MSP_DATAFLASH_SUMMARY"]:
            return struct.pack("<BIII", 3, 16, 1 << 24, len(flash))
        address, size, _ = struct.unpack("<IHB", payload)
        block = flash[address : address + min(size, 1000)]
        return struct.pack("<IHB", address, len(block), 0) + block

    assert dataflash.read(MspClient(FakeMsp(fc))) == flash


def _fc_2026_6(code: int, payload: bytes) -> bytes:
    """An FC running 2026.6.2 that accepts every request with an empty reply."""
    return bytes([26, 6, 2, 8]) + b"2026.6.2" if code == CODES["MSP_FC_VERSION"] else b""


def _run(monkeypatch: pytest.MonkeyPatch, *request: str, fake: FakeMsp | None = None, **options: Any) -> list[str]:
    """`fpv msp` against a fake FC; the names of the messages it sent, other than the version query."""
    fake = fake or FakeMsp(_fc_2026_6)

    def open_port(*_: object) -> contextlib.nullcontext[FakeMsp]:
        return contextlib.nullcontext(fake)

    monkeypatch.setattr(fc_app, "open_port", open_port)
    fc_app.msp(*request, **options)
    return [NAMES[code] for code, _ in fake.requests if code != CODES["MSP_FC_VERSION"]]


def test_changes_in_ram_are_undone_unless_saved_or_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    text = ("MSP2_SET_TEXT", "type=CRAFT_NAME", "text=HI")
    assert _run(monkeypatch, *text)[-1] == "MSP_REBOOT"
    saved = _run(monkeypatch, *text, save=True)
    assert saved[-1] == "MSP_EEPROM_WRITE" and "MSP_REBOOT" not in saved
    assert not {"MSP_REBOOT", "MSP_EEPROM_WRITE"} & set(_run(monkeypatch, *text, no_reboot=True))
    assert not {"MSP_REBOOT", "MSP_EEPROM_WRITE"} & set(_run(monkeypatch, "MSP_RC"))
    # Settings changes whose names do not say so
    assert _run(monkeypatch, "MSP_SELECT_SETTING", "1", struct="B")[-1] == "MSP_REBOOT"


def test_runtime_state_is_undone_but_not_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, "MSP_SET_RTC", "0", "0", struct="<IH")[-1] == "MSP_REBOOT"
    with pytest.warns(UserWarning, match="no motor test timeout"):
        sent = _run(monkeypatch, "MSP_SET_MOTOR", no_reboot=True)
    assert "MSP_REBOOT" not in sent
    with pytest.warns(UserWarning, match="MSP_SET_MOTOR is not a setting"):
        sent = _run(monkeypatch, "MSP_SET_MOTOR", save=True)
    assert not {"MSP_REBOOT", "MSP_EEPROM_WRITE"} & set(sent)


def test_a_reboot_reply_lost_to_the_usb_drop_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    # msp_serial.c reboots once its transmit buffer is empty, which over USB is before the host has the reply
    # (seen on a real FC)
    class Dropping(FakeMsp):
        def read(self, size: int = 1, /) -> bytes:
            if self.requests[-1][0] == CODES["MSP_REBOOT"]:
                raise OSError("device reports readiness to read but returned no data")
            return super().read(size)

    assert _run(monkeypatch, "MSP_SET_MOTOR", fake=Dropping(_fc_2026_6)) == ["MSP_SET_MOTOR", "MSP_REBOOT"]
    assert _run(monkeypatch, "MSP_REBOOT", fake=Dropping(_fc_2026_6)) == ["MSP_REBOOT"]


def test_irreversible_messages_ask_first(monkeypatch: pytest.MonkeyPatch) -> None:
    # Design: ask on the terminal, Enter means no; without a terminal, only --yes goes ahead
    monkeypatch.setattr(sys, "stdin", Terminal("\n"))
    assert _run(monkeypatch, "MSP_DATAFLASH_ERASE") == []
    monkeypatch.setattr(sys, "stdin", Terminal("y\n"))
    assert _run(monkeypatch, "MSP_DATAFLASH_ERASE") == ["MSP_DATAFLASH_ERASE"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # a pipe
    with pytest.raises(ValueError, match="pass --yes"):
        _run(monkeypatch, "MSP_DATAFLASH_ERASE")
    assert _run(monkeypatch, "MSP_DATAFLASH_ERASE", yes=True) == ["MSP_DATAFLASH_ERASE"]


class Terminal(io.StringIO):
    """Typed input on an interactive stdin."""

    def isatty(self) -> bool:
        return True


def test_every_inbound_message_has_a_known_effect() -> None:
    assert EFFECTS.keys() >= INBOUND
    assert EFFECTS.keys() <= CODES.keys()


def test_fields_from_the_command_line() -> None:
    # Design: numbers in any base, enum names, text, and lists comma-separated
    layout = c.Struct(
        "mode" / c.Enum(c.Int8ul, A=1, B=2), "ids" / c.Array(3, c.Int16ul), "name" / c.PascalString(c.Int8ul, "ascii")
    )
    assert (
        codec.build(layout, {"mode": "B", "ids": "1,0x10,300", "name": "HI"})
        == bytes([2, 1, 0, 16, 0, 44, 1, 2]) + b"HI"
    )


def test_layouts_on_real_replies() -> None:
    # Captured from a 2026.6.0-alpha FC
    vtx = bytes([3, 2, 1, 5, 0]) + (5733).to_bytes(2, "little") + bytes([1, 0, 0, 0, 1, 6, 8, 5])
    reply = codec.parse(PROFILES[2026, 6].msp["MSP_VTX_CONFIG"].reply, vtx)
    assert reply["frequency"] == 5733 and reply["vtxtable_power_levels"] == 5
    # Custom OSD messages exist from 2025.12; 4.5 knows only text types 1..6
    assert codec.build(PROFILES[2026, 6].msp["MSP2_SET_TEXT"].request, {"type": "CUSTOM_MSG_0", "text": "HI"}) == (
        b"\x07\x02HI"
    )
    with pytest.raises(codec.CodecError):
        codec.build(PROFILES[4, 5].msp["MSP2_SET_TEXT"].request, {"type": "CUSTOM_MSG_0", "text": "HI"})
