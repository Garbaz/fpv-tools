"""MSP payload layouts per release line, from the serialisation code in msp/msp.c, and what their values mean.

The structs are the bytes as sent; meanings come from the firmware's tables (generated per line) or from what
msp.c and the code it calls state about a value (anchored in scripts/firmware_anchors.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import construct as c

from fpv_tools.firmware.layout import (
    AMPERE,
    BYTE,
    DEGREE,
    MAH,
    MHZ,
    US,
    VOLT,
    Flags,
    Meaning,
    MspLayout,
    Names,
    Positions,
    indexed,
    scaled,
)

if TYPE_CHECKING:
    from fpv_tools.firmware import Profile

# msp/msp_protocol_v2_betaflight.h MSP2TEXT_*, per release line
TEXT_TYPES = {
    (4, 5): {
        "PILOT_NAME": 1,
        "CRAFT_NAME": 2,
        "PID_PROFILE_NAME": 3,
        "RATE_PROFILE_NAME": 4,
        "BUILDKEY": 5,
        "RELEASENAME": 6,
    },
}
TEXT_TYPES[(2025, 12)] = TEXT_TYPES[(4, 5)] | {f"CUSTOM_MSG_{i}": 7 + i for i in range(4)}  # OSD_CUSTOM_MSG_COUNT 4
TEXT_TYPES[(2026, 6)] = TEXT_TYPES[(2025, 12)] | {"BATTERY_PROFILE_NAME": 11}
# msp/msp.c MSP_REBOOT_*
REBOOT_MODES = ("FIRMWARE", "BOOTLOADER_ROM", "MSC", "MSC_UTC", "BOOTLOADER_FLASH")
PSTRING = c.PascalString(c.Int8ul, "ascii")  # sbufWritePString


def text(n: int | None = None) -> c.StringEncoded:  # sbufWriteData / sbufWriteString of ASCII
    return c.PaddedString(n, "ascii") if n else c.GreedyString("ascii")


u8, u16, u32, i16 = c.Int8ul, c.Int16ul, c.Int32ul, c.Int16sl


def layouts(profile: Profile) -> dict[str, MspLayout]:
    """Message layouts for a release line. Compared across the 4.5, 2025.12 and 2026.6 maintenance heads: the
    bytes are the same except where a line overrides below (msp/msp.c; line numbers at 2026.6.0-alpha e92c10887)."""
    out = _base(profile)
    if profile.line == (4, 5):
        # 4.5 sends FC_VERSION_MAJOR/MINOR/PATCH_LEVEL and no version string
        out["MSP_FC_VERSION"] = MspLayout(reply=c.Struct("major" / u8, "minor" / u8, "patch" / u8))
    return out


def _base(profile: Profile) -> dict[str, MspLayout]:
    text_type = {"type": Names({v: k for k, v in TEXT_TYPES[profile.line].items()})}
    box = Names(profile.permanent_box_ids)  # modes over MSP are numbered by permanent ID (msp_box.c)
    # Mode ranges: auxChannelIndex, and steps of 25 us from CHANNEL_RANGE_MIN (MODE_STEP_TO_CHANNEL_VALUE) up to
    # MAX_MODE_RANGE_STEP. Channel values are pulse widths in us (rx.h PWM_PULSE_MIN, rcData)
    low, high = profile.channel_range
    step = scaled("25", US, low, (0, (high - low) // 25))
    ranges: dict[str, Meaning] = {"aux_channel": indexed(profile.aux_channels), "start": step, "end": step}
    mhz = scaled("1", MHZ)  # vtx.h: freq in MHz
    return {
        "MSP_API_VERSION": MspLayout(reply=c.Struct("protocol" / u8, "api_major" / u8, "api_minor" / u8)),  # 654
        "MSP_FC_VARIANT": MspLayout(reply=c.Struct("variant" / text(4))),  # 660
        "MSP_FC_VERSION": MspLayout(  # 664: year - 2000, month, patch, version string
            reply=c.Struct("year" / u8, "month" / u8, "patch" / u8, "version" / PSTRING)
        ),
        "MSP_BOARD_INFO": MspLayout(  # 677
            reply=c.Struct(
                "board_identifier" / text(4),
                "hardware_revision" / u16,
                "board_type" / u8,
                "target_capabilities" / u8,
                "target_name" / PSTRING,
                "board_name" / PSTRING,
                "manufacturer_id" / PSTRING,
                "rest" / c.GreedyBytes,
            )
        ),
        "MSP_NAME": MspLayout(reply=c.Struct("craft_name" / text())),  # 1207
        "MSP_ANALOG": MspLayout(  # 786
            reply=c.Struct("vbat_legacy" / u8, "mah_drawn" / u16, "rssi" / u16, "amperage" / i16, "vbat" / u16),
            meaning={
                "vbat_legacy": scaled("0.1", VOLT),
                "mah_drawn": scaled("1", MAH),
                "amperage": scaled("0.01", AMPERE),
                "vbat": scaled("0.01", VOLT),
            },
        ),
        "MSP_ATTITUDE": MspLayout(  # 1364
            reply=c.Struct("roll" / i16, "pitch" / i16, "yaw" / i16),
            meaning={"roll": scaled("0.1", DEGREE), "pitch": scaled("0.1", DEGREE), "yaw": scaled("1", DEGREE)},
        ),
        "MSP_RC": MspLayout(  # 1358: rcData, in rc_alias_e order
            reply=c.Struct("channels" / c.GreedyRange(u16)),
            meaning={"channels": Positions(profile.rc_channels, US)},
        ),
        "MSP_DEBUG": MspLayout(reply=c.Struct("debug" / c.GreedyRange(i16))),  # 794: int16_t debug[8]
        "MSP_MODE_RANGES": MspLayout(  # 1465
            reply=c.Struct(
                "ranges" / c.GreedyRange(c.Struct("mode" / u8, "aux_channel" / u8, "start" / u8, "end" / u8))
            ),
            meaning={"mode": box, **ranges},
        ),
        "MSP_SET_MODE_RANGE": MspLayout(  # 2933; the optional logic and linked mode that may follow are not here
            request=c.Struct("index" / u8, "mode" / u8, "aux_channel" / u8, "start" / u8, "end" / u8),
            meaning={"mode": box, **ranges},
        ),
        "MSP_BOXNAMES": MspLayout(request=c.Struct("page" / u8), reply=c.Struct("names" / text())),  # 2401
        "MSP_BOXIDS": MspLayout(  # 2407
            request=c.Struct("page" / u8), reply=c.Struct("modes" / c.GreedyRange(u8)), meaning={"modes": box}
        ),
        "MSP_VTX_CONFIG": MspLayout(  # 2194
            reply=c.Struct(
                "device_type" / u8,
                "band" / u8,
                "channel" / u8,
                "power" / u8,
                "pit_mode" / u8,
                "frequency" / u16,
                "device_ready" / u8,
                "low_power_disarm" / u8,
                "pit_mode_frequency" / u16,
                "vtxtable_available" / u8,
                "vtxtable_bands" / u8,
                "vtxtable_channels" / u8,
                "vtxtable_power_levels" / u8,
            ),
            meaning={
                "device_type": Names(profile.vtx_device_types),
                "frequency": mhz,
                "low_power_disarm": indexed(profile.vtx_low_power_disarm),  # the vtx_low_power_disarm setting
                "pit_mode_frequency": mhz,
            },
        ),
        "MSP_VTXTABLE_BAND": MspLayout(  # 2488
            request=c.Struct("band" / u8),
            reply=c.Struct(
                "band" / u8,
                "name" / PSTRING,
                "letter" / text(1),
                "factory" / u8,
                "frequencies" / c.PrefixedArray(u8, u16),
            ),
            meaning={"frequencies": mhz},
        ),
        "MSP_VTXTABLE_POWERLEVEL": MspLayout(  # 2512
            request=c.Struct("level" / u8), reply=c.Struct("level" / u8, "value" / u16, "label" / PSTRING)
        ),
        "MSP_DATAFLASH_SUMMARY": MspLayout(  # 520
            reply=c.Struct("flags" / u8, "sectors" / u32, "total_size" / u32, "used_size" / u32),
            meaning={
                "flags": Flags(profile.flashfs_flags),
                "total_size": scaled("1", BYTE),
                "used_size": scaled("1", BYTE),
            },
        ),
        "MSP_DATAFLASH_READ": MspLayout(  # 2816; uncompressed reply (fc/dataflash.py handles reads)
            request=c.Struct("address" / u32, "size" / u16, "allow_compression" / u8),
            reply=c.Struct("address" / u32, "size" / u16, "compression" / u8, "data" / c.GreedyBytes),
        ),
        "MSP_REBOOT": MspLayout(  # 2413
            request=c.Struct("mode" / u8), reply=c.Struct("mode" / u8), meaning={"mode": indexed(REBOOT_MODES)}
        ),
        "MSP2_GET_TEXT": MspLayout(  # 2638
            request=c.Struct("type" / u8), reply=c.Struct("type" / u8, "text" / PSTRING), meaning=text_type
        ),
        "MSP2_SET_TEXT": MspLayout(request=c.Struct("type" / u8, "text" / PSTRING), meaning=text_type),  # 4298
    }
