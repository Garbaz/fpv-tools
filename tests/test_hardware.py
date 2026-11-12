"""What the fakes take as given, checked on a real flight controller. Opt in with the FC's serial port:

    FPV_TEST_PORT=/dev/ttyACM0 uv run pytest -m hardware

Read-only: MSP requests that only read (none with an effect in firmware/msp_effects.py), and one CLI session with
read commands, left with `exit noreboot`. The firmware then keeps arming blocked until the FC reboots (cliEnter);
nothing changes settings, flash or outputs, and nothing runs while the FC is armed. Expected values come from the
firmware source, never from the FC under test: any Betaflight FC of a supported release line should pass.
"""

import os
import struct
from collections.abc import Iterator

import construct as c
import pytest
import serial

from fpv_tools.fc import dataflash
from fpv_tools.fc.console import NO_REBOOT, End, Session
from fpv_tools.fc.link import VCP_IDS, open_port, usb_identity
from fpv_tools.fc.msp import MspClient, MspError
from fpv_tools.fc.msp_codes import CODES
from fpv_tools.firmware import Profile, profile, version_from_msp
from fpv_tools.firmware.msp_effects import effect
from fpv_tools.settings import ConfigExport

pytestmark = pytest.mark.hardware


@pytest.fixture(scope="module")
def port() -> str:
    if not (port := os.environ.get("FPV_TEST_PORT")):
        pytest.skip("FPV_TEST_PORT not set")
    return port


@pytest.fixture
def link(port: str) -> Iterator[serial.Serial]:
    with open_port(port, timeout=2.0) as link:
        status = MspClient(link).request(CODES["MSP_STATUS"])
        # The flight mode flags (offset 6) start with ARM, which is always an active box (msp_box.c)
        if struct.unpack_from("<I", status, 6)[0] & 1:
            pytest.exit("the flight controller is armed: not testing", returncode=1)
        yield link


@pytest.fixture
def fc_profile(link: serial.Serial) -> Profile:
    version = version_from_msp(MspClient(link).request(CODES["MSP_FC_VERSION"]))
    return profile(version, source="flight controller", ignore_mismatch=True)


def test_usb_ids(port: str) -> None:
    # The virtual COM port's IDs, which port detection looks for (firmware USB descriptors)
    identity = usb_identity(port)
    if identity is None:
        pytest.skip(f"{port} is not a USB port")
    assert (identity.vid, identity.pid) in VCP_IDS


def test_msp_v2_frames(link: serial.Serial) -> None:
    # MSP v2 frames for v1 codes are answered in kind: FC_FIRMWARE_IDENTIFIER (build/version.h)
    assert MspClient(link).request(CODES["MSP_FC_VARIANT"]) == b"BTFL"


def test_a_refused_request_is_an_error_frame(link: serial.Serial) -> None:
    # MSP2_GET_TEXT with a text type the firmware does not have: MSP_RESULT_ERROR (msp.c), sent as a '!' frame
    with pytest.raises(MspError, match="rejected"):
        MspClient(link).request(CODES["MSP2_GET_TEXT"], b"\x00")


def test_layouts_read_whole_replies(link: serial.Serial, fc_profile: Profile) -> None:
    # Layouts of read-only requests without fields account for every byte of the FC's reply. Requests the build
    # does not support (not compiled in) are refused and left out.
    fc = MspClient(link)
    read = {n: lay for n, lay in fc_profile.msp.items() if effect(n) is None and not lay.request.subcons}
    checked = 0
    for name, layout in read.items():
        try:
            reply = fc.request(CODES[name])
        except MspError:
            continue
        c.Struct(*layout.reply.subcons, c.Terminated).parse(reply)
        checked += 1
    assert checked >= 3  # MSP_API_VERSION, MSP_FC_VARIANT and MSP_FC_VERSION are always built in


def test_dataflash_reads_as_the_fakes_assume(link: serial.Serial) -> None:
    # serializeDataflashReadReply (msp.c): address echoed, no compression unless asked, at most
    # MSP_PORT_DATAFLASH_BUFFER_SIZE (msp_serial.h), and an empty block past the end of the volume
    fc = MspClient(link)
    info = dataflash.summary(fc)
    if not info.supported:
        pytest.skip("no onboard flash")
    for address, expected_max in ((0, dataflash.BLOCK_SIZE), (info.total_size, 0)):
        reply = fc.request(CODES["MSP_DATAFLASH_READ"], struct.pack("<IHB", address, dataflash.BLOCK_SIZE, 0))
        got, length, compression = struct.unpack_from("<IHB", reply)
        assert (got, compression) == (address, 0) and length == len(reply) - 7 <= expected_max


def test_cli_session_reads_to_the_end_and_leaves_without_reboot(link: serial.Serial) -> None:
    msp_version = version_from_msp(MspClient(link).request(CODES["MSP_FC_VERSION"]))
    session = Session(link, timeout=30.0).__enter__()
    try:
        # A dump's comment lines start with the prompt's characters; it ends with `save` (cli.c printConfig)
        dump = session.run("dump all")
        assert dump.rstrip().endswith("save")
        assert ConfigExport.parse(dump, "flight controller").version == msp_version
    finally:
        closing = session.close(End.KEEP) if session.active else ""
    # `exit noreboot`: the FC answers it, stays on the port and is back in MSP mode
    assert closing.rstrip().endswith(NO_REBOOT)
    assert MspClient(link).request(CODES["MSP_FC_VARIANT"]) == b"BTFL"
