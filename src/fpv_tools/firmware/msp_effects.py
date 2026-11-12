"""What MSP messages change on the FC, and so what `fpv msp` has to do about it at the end.

Read by hand from each message's handler in msp/msp.c (line numbers at 2026.6) and what it calls. Every message the
firmware's inbound handlers take (fc/msp_codes.py INBOUND, generated) must be listed; tests check that. Messages
answered by the outbound handlers only read, except those listed here with a note on why.
"""

from dataclasses import dataclass
from enum import Enum


class Kind(Enum):
    SETTINGS = "changes settings in RAM: a reboot drops them, MSP_EEPROM_WRITE stores them"
    RUNTIME = "changes runtime state until reboot or power-off; not a setting, so MSP_EEPROM_WRITE does not store it"
    SAVES = "writes all settings to flash, including changes not saved before"
    PERSISTENT = "writes to a device outside the FC's settings; a reboot does not undo it"
    IRREVERSIBLE = "deletes or fixes data for good"
    ACTION = "acts once and leaves nothing behind"
    PASSTHROUGH = "hands the port over to another device until the FC reboots"


@dataclass(frozen=True)
class Effect:
    kind: Kind
    note: str = ""  # what is left behind if the FC is not rebooted, where that matters
    reboots: bool = False  # the FC reboots by itself after replying


S, R = Effect(Kind.SETTINGS), Effect(Kind.RUNTIME)

EFFECTS: dict[str, Effect] = {
    # mspProcessInCommand / mspCommonProcessInCommand: *Mutable() setting writes
    **dict.fromkeys(
        (
            "MSP_SET_NAME",
            "MSP_SET_BATTERY_CONFIG",
            "MSP_SET_MODE_RANGE",
            "MSP_SET_FEATURE_CONFIG",
            "MSP_SET_BOARD_ALIGNMENT_CONFIG",
            "MSP_SET_CURRENT_METER_CONFIG",
            "MSP_SET_MIXER_CONFIG",
            "MSP_SET_RX_CONFIG",
            "MSP_SET_LED_COLORS",
            "MSP_SET_LED_STRIP_CONFIG",
            "MSP_SET_RSSI_CONFIG",
            "MSP_SET_ADJUSTMENT_RANGE",
            "MSP_SET_CF_SERIAL_CONFIG",
            "MSP_SET_VOLTAGE_METER_CONFIG",
            "MSP_SET_PID_CONTROLLER",
            "MSP_SET_ARMING_CONFIG",
            "MSP_SET_RX_MAP",
            "MSP_SET_FAILSAFE_CONFIG",
            "MSP_SET_RXFAIL_CONFIG",
            "MSP_SET_BLACKBOX_CONFIG",
            "MSP_SET_TRANSPONDER_CONFIG",
            "MSP_SET_OSD_CONFIG",
            "MSP_SET_VTX_CONFIG",
            "MSP_SET_ADVANCED_CONFIG",
            "MSP_SET_FILTER_CONFIG",
            "MSP_SET_PID_ADVANCED",
            "MSP_SET_SENSOR_CONFIG",
            "MSP_SET_SIMPLIFIED_TUNING",
            "MSP_SET_OSD_CANVAS",  # canvas size, and switches video_system/displayPortDevice to HD/MSP
            "MSP_SET_BEEPER_CONFIG",
            "MSP_SET_PID",
            "MSP_SET_RC_TUNING",
            "MSP_SET_SERVO_CONFIGURATION",
            "MSP_SET_MOTOR_3D_CONFIG",
            "MSP_SET_RC_DEADBAND",
            "MSP_SET_RESET_CURR_PID",  # resetPidProfile(currentPidProfile)
            "MSP_SET_SENSOR_ALIGNMENT",
            "MSP_SET_LED_STRIP_MODECOLOR",
            "MSP_SET_MOTOR_CONFIG",
            "MSP_SET_GPS_CONFIG",
            "MSP_SET_COMPASS_CONFIG",
            "MSP_SET_GPS_RESCUE",
            "MSP_SET_GPS_RESCUE_PIDS",
            "MSP_SET_VTXTABLE_BAND",
            "MSP_SET_VTXTABLE_POWERLEVEL",
            "MSP_SET_ACC_TRIM",
            "MSP_SET_SERVO_MIX_RULE",
            "MSP2_COMMON_SET_SERIAL_CONFIG",
            "MSP2_SET_MOTOR_OUTPUT_REORDERING",
            "MSP2_SET_TEXT",
            "MSP2_SET_LED_STRIP_CONFIG_VALUES",
            "MSP2_SET_BATTERY_PROFILE",
            "MSP_SELECT_SETTING",  # changePidProfile & co.: the profile indices in systemConfig, not saved
            "MSP_COPY_PROFILE",  # pidCopyProfile / copyControlRateProfile in RAM
        ),
        S,
    ),
    "MSP_SET_MOTOR": Effect(Kind.RUNTIME, "the motors keep the outputs sent: the firmware has no motor test timeout"),
    "MSP_SET_ARMING_DISABLED": Effect(Kind.RUNTIME, "arming stays blocked or unblocked as sent"),
    "MSP_SET_RTC": R,  # rtcSet: the clock runs on until power-off or reboot
    "MSP_SET_TX_INFO": R,  # setRssiMsp
    "MSP_SET_HEADING": R,  # magHold
    "MSP_SET_RAW_RC": R,  # rxMspFrameReceive: RC input until the MSP receiver times out
    "MSP_SET_RAW_GPS": R,  # GPS data in place of a GPS receiver
    "MSP2_SENSOR_GPS": R,
    "MSP2_SENSOR_RANGEFINDER_LIDARMT": R,
    "MSP2_SENSOR_OPTICALFLOW_MT": R,
    "MSP_EEPROM_WRITE": Effect(Kind.SAVES),
    "MSP_ACC_CALIBRATION": Effect(Kind.SAVES),  # sensors/acceleration_init.c: saveConfigAndNotify when done
    "MSP_MAG_CALIBRATION": Effect(Kind.SAVES),  # sensors/compass.c: saveConfigAndNotify when done
    "MSP2_BETAFLIGHT_BIND": Effect(Kind.SAVES),  # receivers store the binding with writeEEPROM/saveConfigAndNotify
    "MSP_OSD_CHAR_WRITE": Effect(Kind.PERSISTENT),  # the OSD's font memory
    "MSP2_SEND_DSHOT_COMMAND": Effect(Kind.PERSISTENT),  # to the ESCs; some commands save ESC settings
    "MSP_CAMERA_CONTROL": Effect(Kind.ACTION),  # a camera key press
    "MSP_DATAFLASH_ERASE": Effect(Kind.IRREVERSIBLE, "erases the blackbox logs on the onboard flash"),
    "MSP_SET_BOARD_INFO": Effect(Kind.IRREVERSIBLE, "stores board name and manufacturer once; they cannot be changed"),
    "MSP_SET_SIGNATURE": Effect(Kind.IRREVERSIBLE, "stores the board signature once; it cannot be changed"),
    # Answered by the outbound handlers, but not read-only:
    "MSP_REBOOT": Effect(Kind.ACTION, reboots=True),  # mspRebootFn after the reply
    "MSP_RESET_CONF": Effect(Kind.IRREVERSIBLE, "resets all settings to defaults and saves them", reboots=True),
    "MSP_SET_PASSTHROUGH": Effect(Kind.PASSTHROUGH),  # mspFcProcessCommand: serial or ESC passthrough
}


def effect(name: str) -> Effect | None:
    """The effect of a message by firmware name; None for messages that only read."""
    return EFFECTS.get(name)
