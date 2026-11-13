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
from fpv_tools.firmware.layout import Names
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
    """`fpv-tools msp` against a fake FC; the names of the messages it sent, other than the version query."""
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
    # Design: numbers in any base, text, and lists comma-separated; names where the field has them
    layout = c.Struct("mode" / c.Int8ul, "ids" / c.Array(3, c.Int16ul), "name" / c.PascalString(c.Int8ul, "ascii"))
    meaning = {"mode": Names({1: "A", 2: "B"})}
    assert codec.build(layout, {"mode": "B", "ids": "1,0x10,300", "name": "HI"}, meaning) == (
        bytes([2, 1, 0, 16, 0, 44, 1, 2]) + b"HI"
    )
    # Values without a name are given as numbers, as they are printed
    assert codec.build(layout, {"mode": "7"}, meaning) == bytes([7])


def test_values_are_what_the_firmware_means() -> None:
    profile = PROFILES[2026, 6]
    # This FC's CLI prints "aux 3 13 3 1700 2100 0 0": the beeper (permanent ID 13, msp_box.c) on AUX4 (aux channel
    # index 3) from 1700 to 2100 us. MSP carries the range in steps: (us - 900) / 25 (CHANNEL_VALUE_TO_STEP).
    ranges = profile.msp["MSP_MODE_RANGES"]
    assert codec.parse(ranges.reply, bytes([13, 3, 32, 48]), ranges.meaning) == {
        "ranges": [{"mode": "BEEPERON", "aux_channel": "AUX4", "start": 1700, "end": 2100}]
    }
    # Design: --raw is the values as sent
    assert codec.parse(ranges.reply, bytes([13, 3, 32, 48])) == {
        "ranges": [{"mode": 13, "aux_channel": 3, "start": 32, "end": 48}]
    }
    # Setting it takes the same values (msp.c MSP_SET_MODE_RANGE reads index, permanent ID, aux index, steps)
    set_range = profile.msp["MSP_SET_MODE_RANGE"]
    fields = {"index": "3", "mode": "BEEPERON", "aux_channel": "AUX4", "start": "1700", "end": "2100"}
    assert codec.build(set_range.request, fields, set_range.meaning) == bytes([3, 13, 3, 32, 48])
    # Design: input that is not exact is refused, never rounded; and a number that has a name is refused, as it
    # could be meant in another numbering (AUX4 is index 3)
    with pytest.raises(codec.CodecError, match="steps of 25 us"):
        codec.build(set_range.request, fields | {"start": "1710"}, set_range.meaning)
    with pytest.raises(codec.CodecError, match="AUX4"):
        codec.build(set_range.request, fields | {"aux_channel": "3"}, set_range.meaning)


def test_rc_channels_are_named_in_firmware_order() -> None:
    # rcData is in rc_alias_e order: roll, pitch, yaw, throttle, then the aux channels
    rc = PROFILES[2026, 6].msp["MSP_RC"]
    values = [1500, 1501, 1502, 990, 1000, 1508, 2000]
    reply = codec.parse(rc.reply, b"".join(v.to_bytes(2, "little") for v in values), rc.meaning)
    assert reply == {
        "channels": {
            "ROLL": 1500,
            "PITCH": 1501,
            "YAW": 1502,
            "THROTTLE": 990,
            "AUX1": 1000,
            "AUX2": 1508,
            "AUX3": 2000,
        }
    }


def test_layouts_on_real_replies() -> None:
    # Captured from a 2026.6.0-alpha FC whose CLI said vtx_low_power_disarm = OFF and vtx_freq = 5733
    vtx = bytes([3, 2, 1, 5, 0]) + (5733).to_bytes(2, "little") + bytes([1, 0, 0, 0, 1, 6, 8, 5])
    layout = PROFILES[2026, 6].msp["MSP_VTX_CONFIG"]
    reply = codec.parse(layout.reply, vtx, layout.meaning)
    assert reply["frequency"] == 5733 and reply["low_power_disarm"] == "OFF" and reply["vtxtable_power_levels"] == 5
    assert layout.units()["frequency"] == "MHz"
    # Custom OSD messages exist from 2025.12; 4.5 knows only text types 1..6
    text_2026, text_4_5 = PROFILES[2026, 6].msp["MSP2_SET_TEXT"], PROFILES[4, 5].msp["MSP2_SET_TEXT"]
    assert codec.build(text_2026.request, {"type": "CUSTOM_MSG_0", "text": "HI"}, text_2026.meaning) == b"\x07\x02HI"
    with pytest.raises(codec.CodecError):
        codec.build(text_4_5.request, {"type": "CUSTOM_MSG_0", "text": "HI"}, text_4_5.meaning)


def test_replies_as_meant_as_sent_or_as_bytes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # MSP_ANALOG (msp.c): legacy voltage in 0.1 V, mAh drawn, RSSI, current in 0.01 A, voltage in 0.01 V
    analog = struct.pack("<BHHhH", 161, 250, 1023, 342, 1612)

    def fc(code: int, payload: bytes) -> bytes:
        return analog if code == CODES["MSP_ANALOG"] else _fc_2026_6(code, payload)

    def lines(**options: Any) -> list[str]:
        _run(monkeypatch, "MSP_ANALOG", fake=FakeMsp(fc), **options)
        return [" ".join(line.split()) for line in capsys.readouterr().out.splitlines()]

    assert lines() == ["vbat_legacy 16.1 V", "mah_drawn 250 mAh", "rssi 1023", "amperage 3.42 A", "vbat 16.12 V"]
    assert lines(raw=True) == ["vbat_legacy 161", "mah_drawn 250", "rssi 1023", "amperage 342", "vbat 1612"]
    assert lines(as_hex=True) == [f"hex={analog.hex()}"]
