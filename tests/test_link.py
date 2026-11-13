import os
from collections.abc import Iterator

import pytest
import serial
from serial.tools.list_ports_common import ListPortInfo

from fpv_tools.fc import link


def _port(device: str, vid: int | None = None, pid: int | None = None, serial: str | None = None) -> ListPortInfo:
    info = ListPortInfo(device, skip_link_detection=True)
    info.vid, info.pid, info.serial_number = vid, pid, serial
    return info


FC = _port("/dev/ttyACM0", 0x0483, 0x5740, "3A0035")
OTHER_FC = _port("/dev/ttyACM1", 0x2E3C, 0x5740, "1F0042")
UART = _port("/dev/ttyUSB0", 0x0403, 0x6001, "A50285BI")  # an FTDI adapter
BUILTIN = _port("/dev/ttyS0")


def _comports(monkeypatch: pytest.MonkeyPatch, *scans: list[ListPortInfo]) -> None:
    """Successive comports() results; the last one repeats."""
    results: Iterator[list[ListPortInfo]] = iter(scans)
    last: list[list[ListPortInfo]] = [[]]

    def comports() -> list[ListPortInfo]:
        last[0] = next(results, last[0])
        return last[0]

    monkeypatch.setattr(link.list_ports, "comports", comports)


def test_waiting_for_the_fc_to_come_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(link, "POLL_S", 0.0)
    _comports(monkeypatch, [FC])
    identity = link.usb_identity(FC.device)
    assert identity is not None and link.usb_identity(BUILTIN.device) is None

    _comports(monkeypatch, [FC], [UART], [UART, _port("/dev/ttyACM3", 0x0483, 0x5740, "3A0035")])
    assert link.await_return(identity) is True  # back, under another device name

    monkeypatch.setattr(link, "BACK_S", 0.0)
    _comports(monkeypatch, [FC], [OTHER_FC])  # gone, and another FC is not it
    assert link.await_return(identity) is False

    monkeypatch.setattr(link, "GONE_S", 0.0)
    _comports(monkeypatch, [FC])
    assert link.await_return(identity) is None  # never left


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX pseudo-terminal; root ignores TIOCEXCL")
def test_the_port_is_kept_from_other_programs() -> None:
    # Design: while fpv-tools talks to the FC, nothing else may (the Betaflight app connecting in between left the
    # FC in its CLI). A pseudo-terminal stands in for the FC's port.
    import pty  # POSIX only

    controller, device = pty.openpty()
    path = os.ttyname(device)
    try:
        with link.open_port(path):
            with pytest.raises(serial.SerialException):  # another program, opening it plainly
                serial.Serial(path)
            with pytest.raises(link.LinkError, match="busy"):  # a second fpv-tools
                link.open_port(path)
        link.open_port(path).close()  # free again once closed
    finally:
        os.close(controller)
        os.close(device)


def test_a_port_in_use_on_windows_is_reported_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    # pyserial on Windows reports a port another program holds with the WinError in its message, no errno
    # (serialwin32.py; ERROR_ACCESS_DENIED maps to PermissionError)
    def in_use(_: object) -> None:
        raise serial.SerialException("could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)")

    monkeypatch.setattr(link._Exclusive, "open", in_use)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(link.LinkError, match="busy"):
        link.open_port("COM3")
