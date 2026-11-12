"""Finding and opening the flight controller's USB serial port, and seeing it come back after a reboot."""

import errno
import os
import time
from typing import NamedTuple

import serial
from serial.tools import list_ports
from serial.tools.list_ports_common import ListPortInfo

# USB vendor/product IDs of the virtual COM port Betaflight's USB stack presents, per MCU platform (firmware
# source: platform/STM32/vcp_hal/usbd_desc.c and vcpf4/usbd_desc.c, lib/main/AT32F43x/.../cdc/cdc_desc.h,
# platform/APM32/usb/vcp/usbd_cdc_descriptor.c, platform/PICO/usb/usb_descriptors.c). Betaflight's app lists ports
# with these among others (src/js/protocols/devices.ts). They are the chip vendors' generic serial IDs: other
# firmware (INAV, other STM32 gadgets) uses them too.
VCP_IDS: dict[tuple[int, int], str] = {
    (0x0483, 0x5740): "STM32",
    (0x2E3C, 0x5740): "AT32",
    (0x314B, 0x5740): "APM32",
    (0x2E8A, 0x000A): "RP2040",
    (0x2E8A, 0x0009): "RP2350",
}

# How long to wait for a rebooting FC to leave USB and to come back. Not firmware facts: generous bounds (one FC
# took 0.7 s for both), so that a slow board is waited for and a missing one is reported eventually.
GONE_S = 3.0
BACK_S = 15.0
POLL_S = 0.1


class LinkError(Exception):
    pass


class UsbIdentity(NamedTuple):
    vid: int
    pid: int
    serial_number: str | None
    location: str | None  # where there is no serial number


def _describe(port: ListPortInfo) -> str:
    return f"{port.device} ({port.description})" if port.description not in (None, "", "n/a") else port.device


def find_port() -> str:
    """The one USB serial port with Betaflight's USB IDs; error if there is none or more than one."""
    candidates = [p for p in list_ports.comports() if (p.vid, p.pid) in VCP_IDS]
    if not candidates:
        raise LinkError("no flight controller on USB (no serial port with Betaflight's USB IDs); pass --port")
    if len(candidates) > 1:
        found = ", ".join(_describe(p) for p in candidates)
        raise LinkError(f"several USB serial ports with Betaflight's USB IDs: {found}; pick one with --port")
    return candidates[0].device


def open_port(port: str | None = None, timeout: float = 1.0) -> serial.Serial:
    port = port or find_port()
    try:
        return serial.Serial(port, 115200, timeout=timeout, write_timeout=timeout)
    except serial.SerialException as e:
        if e.errno == errno.EBUSY:
            raise LinkError(f"{port} is busy: another program (e.g. the Betaflight app) has it open") from e
        raise LinkError(str(e)) from e


def _same(device: str, other: str) -> bool:
    return device == other or (os.name == "posix" and os.path.realpath(device) == os.path.realpath(other))


def _identity(p: ListPortInfo) -> UsbIdentity | None:
    if p.vid is None or p.pid is None:
        return None
    return UsbIdentity(p.vid, p.pid, p.serial_number, None if p.serial_number else p.location)


def usb_identity(port: str | None) -> UsbIdentity | None:
    """What identifies the USB device behind a serial port across a reboot; None if it is not a USB port."""
    return next((_identity(p) for p in list_ports.comports() if port and _same(port, p.device)), None)


def _present(identity: UsbIdentity) -> bool:
    return any(_identity(p) == identity for p in list_ports.comports())


def await_return(identity: UsbIdentity) -> bool | None:
    """Wait for a rebooting FC to leave USB and enumerate again. True once it is back, False if it left but did not
    come back in time (e.g. it rebooted into a bootloader), None if it did not leave."""
    deadline = time.monotonic() + GONE_S
    while _present(identity):
        if time.monotonic() > deadline:
            return None
        time.sleep(POLL_S)
    deadline = time.monotonic() + BACK_S
    while not _present(identity):
        if time.monotonic() > deadline:
            return False
        time.sleep(POLL_S)
    return True
