"""Onboard flash (blackbox storage) over MSP, as the Betaflight app does it.

Reads go in blocks of up to MSP_PORT_DATAFLASH_BUFFER_SIZE (msp/msp_serial.h) without compression; the FC
truncates a block at the end of the volume and answers an empty block past it (msp/msp.c
serializeDataflashReadReply). Erasing runs in the background on the FC; MSP_DATAFLASH_SUMMARY reports
ready again when it is done.
"""

import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from fpv_tools.fc.msp import MspClient, MspError
from fpv_tools.fc.msp_codes import CODES

BLOCK_SIZE = 4096
FLAG_READY = 1  # MSP_FLASHFS_FLAG_READY, msp/msp.c
FLAG_SUPPORTED = 2  # MSP_FLASHFS_FLAG_SUPPORTED


class DataflashError(Exception):
    pass


@dataclass(frozen=True)
class Summary:
    ready: bool
    supported: bool
    sectors: int
    total_size: int
    used_size: int


def summary(fc: MspClient) -> Summary:
    flags, sectors, total, used = struct.unpack("<BIII", fc.request(CODES["MSP_DATAFLASH_SUMMARY"]))
    return Summary(bool(flags & FLAG_READY), bool(flags & FLAG_SUPPORTED), sectors, total, used)


def read(fc: MspClient, progress: Callable[[int, int], None] | None = None, retries: int = 5) -> bytes:
    """All used bytes of the flash. `progress(done, total)` is called after each block."""
    info = summary(fc)
    if not info.supported:
        raise DataflashError("the flight controller has no onboard flash (SD card logging is not supported yet)")
    data = bytearray()
    while len(data) < info.used_size:
        block = _read_block(fc, len(data), min(BLOCK_SIZE, info.used_size - len(data)), retries)
        if not block:
            break  # end of the volume
        data += block
        if progress:
            progress(len(data), info.used_size)
    return bytes(data)


def _read_block(fc: MspClient, address: int, size: int, retries: int) -> bytes:
    """One uncompressed MSP_DATAFLASH_READ: reply is address (u32), length (u16), compression (u8), data."""
    for attempt in range(retries):
        try:
            reply = fc.request(CODES["MSP_DATAFLASH_READ"], struct.pack("<IHB", address, size, 0))
            got, length, compression = struct.unpack_from("<IHB", reply)
            if got != address or compression != 0:
                raise MspError(f"unexpected block (address {got}, compression {compression})")
            return reply[7 : 7 + length]
        except MspError:
            if attempt == retries - 1:
                raise
    raise AssertionError("unreachable")


def erase_timeout(total_size: int) -> float:
    """Worst-case erase time, as the Betaflight app bounds it (getDataflashEraseTimeoutMs): NOR chips take up to
    200 s per 16 MiB; parts above 64 MiB are NAND and erase much faster."""
    unit = 16 * 1024 * 1024
    nor_bytes = min(max(total_size, unit), 64 * 1024 * 1024)
    return math.ceil(nor_bytes / unit) * 200 + 100


def erase(fc: MspClient, progress: Callable[[float], None] | None = None) -> None:
    """Erase the whole flash and wait until the FC reports it ready again."""
    info = summary(fc)
    if not info.supported:
        raise DataflashError("the flight controller has no onboard flash")
    fc.request(CODES["MSP_DATAFLASH_ERASE"])
    start = time.monotonic()
    deadline = start + erase_timeout(info.total_size)
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if progress:
            progress(time.monotonic() - start)
        try:
            if summary(fc).ready:
                return
        except MspError:
            continue  # some chips keep the FC busy during a full erase
    raise DataflashError("erase did not finish in time")
