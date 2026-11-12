"""What users can name in `fpv bb`: signals and conditions. Plain text for the help; imports nothing, so help
stays fast."""

SIGNALS = """\
Signals (AXIS is roll, pitch or yaw; N a motor or debug index):

    t                      seconds since the flight's first frame
    gyro.AXIS              filtered gyro, deg/s
    gyro_raw.AXIS          unfiltered gyro, deg/s
    setpoint.AXIS          rate setpoint, deg/s
    error.AXIS             setpoint - gyro, deg/s
    P.AXIS I.AXIS D.AXIS   PID terms
    F.AXIS                 feedforward term
    rc.AXIS                stick command (rcCommand), -500..500
    sticks                 largest |rc.AXIS|, %
    thr                    mixer throttle, after throttle curve and limit, %
    rc_thr                 throttle stick command, %
    motor.N                motor output, %; also motor.avg, motor.min, motor.max
    acc.x acc.y acc.z      accelerometer, g; acc is the magnitude
    jerk                   rate of change of acceleration, g/s
    vbat                   battery voltage, V
    cell                   battery voltage per cell, V *
    vbat_display           the FC's filtered voltage, as OSD and alarms see it, V *
    battery_state          OK, WARNING or CRITICAL, as the FC's voltage alarm *
    amps                   current, A
    mah                    current drawn since the flight's start, mAh
    rssi                   RSSI as logged, 0..1023
    dbm                    CRSF uplink RSSI, dBm *
    rx                     1 while RC frames arrive
    mode                   active flight modes, e.g. "ANGLE" ("ACRO" if none)
    box.NAME               1 while mode NAME is active, e.g. box.ARM, box.BEEPERON
    failsafe               failsafe phase
    debug.N                debug value as logged (its meaning depends on debug_mode)
    FIELD                  a raw log field, e.g. -s 'gyroADC[0]'

Signals marked * need FC settings the log does not record (--config, --config-from-fc or --set).
"""

CONDITIONS = """\
Conditions (--when) are Python-style expressions, evaluated per sample: signals, numbers, "strings", arithmetic,
comparisons, and / or / not, e.g. 'thr > 5 and mode == "ACRO"' or 'abs(error.roll) > 50'. Functions:

    abs(x)
    smooth(x, s)              x averaged over s seconds around each sample
    widen(c, before, after)   c, extended by before / after seconds around each sample where it holds
    held(c, s)                c held at some point within the last s seconds
    sustained(c, s)           c holds throughout the s seconds around the sample
"""
