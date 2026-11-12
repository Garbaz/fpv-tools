"""MSP payload layouts per release line, from the serialisation code in msp/msp.c."""

import construct as c

from fpv_tools.firmware.layout import MspLayout

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
REBOOT_MODE = c.Enum(c.Int8ul, FIRMWARE=0, BOOTLOADER_ROM=1, MSC=2, MSC_UTC=3, BOOTLOADER_FLASH=4)
PSTRING = c.PascalString(c.Int8ul, "ascii")  # sbufWritePString


def text(n: int | None = None) -> c.StringEncoded:  # sbufWriteData / sbufWriteString of ASCII
    return c.PaddedString(n, "ascii") if n else c.GreedyString("ascii")


u8, u16, u32, i16 = c.Int8ul, c.Int16ul, c.Int32ul, c.Int16sl


def layouts(line: tuple[int, int]) -> dict[str, MspLayout]:
    """Message layouts for a release line. Compared across the 4.5, 2025.12 and 2026.6 maintenance heads: the
    bytes are the same except where a line overrides below (msp/msp.c; line numbers at 2026.6)."""
    text_type = c.Enum(c.Int8ul, **TEXT_TYPES[line])
    out = _base(text_type)
    if line == (4, 5):
        # 4.5 sends FC_VERSION_MAJOR/MINOR/PATCH_LEVEL and no version string
        out["MSP_FC_VERSION"] = MspLayout(reply=c.Struct("major" / u8, "minor" / u8, "patch" / u8))
    return out


def _base(text_type: c.Enum) -> dict[str, MspLayout]:
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
        "MSP_ANALOG": MspLayout(  # 786: legacy vbat in 0.1 V, amperage in 0.01 A, vbat in 0.01 V
            reply=c.Struct("vbat_legacy" / u8, "mah_drawn" / u16, "rssi" / u16, "amperage" / i16, "vbat" / u16)
        ),
        "MSP_ATTITUDE": MspLayout(  # 1364: roll and pitch in 0.1 deg, yaw in deg
            reply=c.Struct("roll" / i16, "pitch" / i16, "yaw" / i16)
        ),
        "MSP_RC": MspLayout(reply=c.Struct("channels" / c.GreedyRange(u16))),
        "MSP_DEBUG": MspLayout(reply=c.Struct("debug" / c.GreedyRange(i16))),  # 794: int16_t debug[8]
        "MSP_MODE_RANGES": MspLayout(  # 1465
            reply=c.Struct(
                "ranges"
                / c.GreedyRange(c.Struct("permanent_id" / u8, "aux_channel" / u8, "start_step" / u8, "end_step" / u8))
            )
        ),
        "MSP_BOXNAMES": MspLayout(request=c.Struct("page" / u8), reply=c.Struct("names" / text())),  # 2401
        "MSP_BOXIDS": MspLayout(request=c.Struct("page" / u8), reply=c.Struct("permanent_ids" / c.GreedyRange(u8))),
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
            )
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
        ),
        "MSP_VTXTABLE_POWERLEVEL": MspLayout(  # 2512
            request=c.Struct("level" / u8), reply=c.Struct("level" / u8, "value" / u16, "label" / PSTRING)
        ),
        "MSP_DATAFLASH_SUMMARY": MspLayout(  # 520: flags bit 0 ready, bit 1 supported
            reply=c.Struct("flags" / u8, "sectors" / u32, "total_size" / u32, "used_size" / u32)
        ),
        "MSP_DATAFLASH_READ": MspLayout(  # 2816; uncompressed reply (fc/dataflash.py handles reads)
            request=c.Struct("address" / u32, "size" / u16, "allow_compression" / u8),
            reply=c.Struct("address" / u32, "size" / u16, "compression" / u8, "data" / c.GreedyBytes),
        ),
        "MSP_REBOOT": MspLayout(request=c.Struct("mode" / REBOOT_MODE), reply=c.Struct("mode" / REBOOT_MODE)),  # 2413
        "MSP2_GET_TEXT": MspLayout(  # 2638
            request=c.Struct("type" / text_type), reply=c.Struct("type" / text_type, "text" / PSTRING)
        ),
        "MSP2_SET_TEXT": MspLayout(request=c.Struct("type" / text_type, "text" / PSTRING)),  # 4298
    }
