"""Firmware statements fpv-tools' own logic reproduces, keyed by the fpv-tools code that relies on them.

scripts/gen_firmware_tables.py checks every anchor against every release line before writing its tables: the code,
with comments removed and whitespace normalised, must occur exactly once in the file. Any failure stops generation
for that line; the fix is to read the firmware change and update the fpv-tools code named in the key (and the
anchor), never to loosen the anchor until it passes.

What this does not prove: that the logic around an anchor is unchanged. Anchors are chosen as the statements the
reproduced semantics hinge on, so that the likely ways of changing them break an anchor.
"""

# An anchor is (file, code) or (file, (code, equivalent code, ...)) when releases phrase the same thing differently.
# A file prefixed with RAW is matched with comments kept: for facts the firmware states only in comments (units).
type Anchor = tuple[str, str | tuple[str, ...]]
RAW = "raw:"

ANCHORS: dict[str, list[Anchor]] = {
    "log units and flags (blackbox/flight.py)": [
        (
            "src/main/blackbox/blackbox.c",
            "memcpy(&slow->flightModeFlags, &rcModeActivationMask, sizeof(slow->flightModeFlags));",
        ),
        ("src/main/blackbox/blackbox.c", "blackboxCurrent->setpoint[3] = lrintf(mixerGetThrottle() * 1000);"),
        (
            "src/main/blackbox/blackbox.c",
            "blackboxCurrent->gyroADC[i] = lrintf(gyro.gyroADCf[i] * blackboxHighResolutionScale);",
        ),
        (
            "src/main/blackbox/blackbox.c",
            "blackboxHighResolutionScale = blackboxConfig()->high_resolution ? 10.0f : 1.0f;",
        ),
        ("src/main/blackbox/blackbox.c", "blackboxCurrent->rssi = getRssi();"),
        (
            "src/main/blackbox/blackbox.c",
            'BLACKBOX_PRINT_HEADER_LINE("motorOutput", "%d,%d", motorOutputLowInt, motorOutputHighInt);',
        ),
        ("src/main/blackbox/blackbox.c", "vbatReference = getBatteryVoltageLatest();"),
    ],
    "blackbox decoder (blackbox/bbl.py)": [
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_0 = 0,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_PREVIOUS = 1,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_STRAIGHT_LINE = 2,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_AVERAGE_2 = 3,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_MINTHROTTLE = 4,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_MOTOR_0 = 5,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_INC = 6,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_HOME_COORD = 7,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_1500 = 8,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_VBATREF = 9,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_LAST_MAIN_FRAME_TIME = 10,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_PREDICTOR_MINMOTOR = 11"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_SIGNED_VB = 0,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_UNSIGNED_VB = 1,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_NEG_14BIT = 3,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_TAG8_8SVB = 6,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_TAG2_3S32 = 7,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_TAG8_4S16 = 8,"),
        ("src/main/blackbox/blackbox_fielddefs.h", "FLIGHT_LOG_FIELD_ENCODING_NULL = 9,"),
        ("src/main/blackbox/blackbox.h", "FLIGHT_LOG_EVENT_SYNC_BEEP = 0,"),
        ("src/main/blackbox/blackbox.h", "FLIGHT_LOG_EVENT_INFLIGHT_ADJUSTMENT = 13,"),
        ("src/main/blackbox/blackbox.h", "FLIGHT_LOG_EVENT_LOGGING_RESUME = 14,"),
        ("src/main/blackbox/blackbox.h", "FLIGHT_LOG_EVENT_DISARM = 15,"),
        ("src/main/blackbox/blackbox.h", "FLIGHT_LOG_EVENT_FLIGHTMODE = 30,"),
        ("src/main/blackbox/blackbox.h", "FLIGHT_LOG_EVENT_LOG_END = 255"),
        (
            "src/main/blackbox/blackbox_fielddefs.h",
            "#define FLIGHT_LOG_EVENT_INFLIGHT_ADJUSTMENT_FUNCTION_FLOAT_VALUE_FLAG 128",
        ),
        (
            "src/main/blackbox/blackbox.c",
            "case FLIGHT_LOG_EVENT_FLIGHTMODE: blackboxWriteUnsignedVB(data->flightMode.flags); blackboxWriteUnsignedVB(data->flightMode.lastFlags); break;",
        ),
        (
            "src/main/blackbox/blackbox.c",
            "case FLIGHT_LOG_EVENT_DISARM: blackboxWriteUnsignedVB(data->disarm.reason); break;",
        ),
        (
            "src/main/blackbox/blackbox.c",
            "case FLIGHT_LOG_EVENT_LOGGING_RESUME: blackboxWriteUnsignedVB(data->loggingResume.logIteration); blackboxWriteUnsignedVB(data->loggingResume.currentTime); break;",
        ),
        (
            "src/main/blackbox/blackbox.c",
            'case FLIGHT_LOG_EVENT_LOG_END: blackboxWriteString("End of log"); blackboxWrite(0); break;',
        ),
        ("src/main/blackbox/blackbox.c", "int32_t predictor = (prev1[i] + prev2[i]) / 2;"),
        (
            "src/main/blackbox/blackbox.c",
            "blackboxWriteSignedVB((int32_t) (blackboxHistory[0]->time - 2 * blackboxHistory[1]->time + blackboxHistory[2]->time));",
        ),
        ("src/main/blackbox/blackbox.c", "return blackboxPFrameIndex == 0 && blackboxPInterval != 0;"),
        (
            "src/main/blackbox/blackbox.c",
            "if (++blackboxLoopIndex >= blackboxIInterval) { blackboxLoopIndex = 0; blackboxIFrameIndex++; blackboxPFrameIndex = 0; } else if (++blackboxPFrameIndex >= blackboxPInterval) { blackboxPFrameIndex = 0; }",
        ),
        ("src/main/blackbox/blackbox.c", 'BLACKBOX_PRINT_HEADER_LINE("P interval", "%d", blackboxPInterval);'),
        (
            "src/main/blackbox/blackbox.c",
            "blackboxHistory[1] = blackboxHistory[0]; blackboxHistory[2] = blackboxHistory[0];",
        ),
        (
            "src/main/blackbox/blackbox.c",
            "blackboxWriteUnsignedVB((vbatReference - blackboxCurrent->vbatLatest) & 0x3FFF);",
        ),
    ],
    "units of vbat and amps (Flight)": [
        ("src/main/blackbox/blackbox.c", "blackboxCurrent->vbatLatest = getBatteryVoltageLatest();"),
        ("src/main/blackbox/blackbox.c", "blackboxCurrent->amperageLatest = getAmperageLatest();"),
        ("src/main/sensors/battery.c", "uint16_t getBatteryVoltageLatest(void) { return voltageMeter.unfiltered; }"),
        (RAW + "src/main/sensors/voltage.h", "uint16_t unfiltered; // voltage in 0.01V steps"),
        ("src/main/sensors/battery.c", "int32_t getAmperageLatest(void) { return currentMeter.amperageLatest; }"),
        (
            RAW + "src/main/sensors/current.h",
            "typedef struct currentMeter_s { int32_t amperage; // current read by current sensor in centiampere (1/100th A) int32_t amperageLatest; // current read by current sensor in centiampere (1/100th A) (unfiltered)",
        ),
    ],
    "current scale (Flight._amps)": [
        (
            "src/main/sensors/current.c",
            "int32_t centiAmps = config->scale ? (millivolts * 10000 / (int32_t)config->scale + (int32_t)config->offset) / 10 : 0;",
        ),
    ],
    "CRSF RSSI in dBm (Flight._dbm)": [
        (
            "src/main/rx/crsf.c",
            (
                "const uint16_t rssiPercentScaled = scaleRange(rssiDbm, CRSF_RSSI_MIN, CRSF_RSSI_MAX, 0, RSSI_MAX_VALUE);",
                # 2026.6 (c39e91154): same linear map in float, truncated to uint16_t
                "DEFINE_SCALE_FN(scaleRangeCrsfRssi, CRSF_RSSI_MIN, CRSF_RSSI_MAX, 0, RSSI_MAX_VALUE)",
            ),
        ),
        ("src/main/rx/crsf.c", "if (rssiSource == RSSI_SOURCE_NONE) { rssiSource = RSSI_SOURCE_RX_PROTOCOL_CRSF; }"),
        ("src/main/rx/rx.c", "if (featureIsEnabled(FEATURE_RSSI_ADC)) { rssiSource = RSSI_SOURCE_ADC; } else"),
        ("src/main/rx/rx.c", "if (rxConfig()->rssi_channel > 0) { rssiSource = RSSI_SOURCE_RX_CHANNEL; }"),
        ("src/main/rx/rx.c", "if (rxConfig()->rssi_invert) { rssiValue = RSSI_MAX_VALUE - rssiValue; }"),
        (
            "src/main/rx/rx.c",
            "return rxConfig()->rssi_scale / 100.0f * rssiValue + rxConfig()->rssi_offset * RSSI_OFFSET_SCALING;",
        ),
    ],
    "battery alarm (blackbox/fcmodel.py)": [
        ("src/main/sensors/battery.h", "#define GET_BATTERY_LPF_FREQUENCY(period) (1 / (period / 10.0f))"),
        ("src/main/common/filter.c", "float omega = 2.0f * M_PIf * f_cut * dT; return omega / (omega + 1.0f);"),
        (
            "src/main/sensors/voltage.c",
            "uint16_t filteredDisplaySample = pt1FilterApply(&state->displayFilter, rawSample);",
        ),
        (
            "src/main/fc/tasks.c",
            '[TASK_BATTERY_ALERTS] = DEFINE_TASK("BATTERY_ALERTS", NULL, NULL, taskBatteryAlerts, TASK_PERIOD_HZ(5), TASK_PRIORITY_MEDIUM),',
        ),
        (
            "src/main/sensors/battery.c",
            "if (cmp32(millis(), lastVoltageChangeMs) >= batteryConfig()->vbatDurationForWarning * 100) { voltageState = BATTERY_WARNING; }",
        ),
        (
            "src/main/sensors/battery.c",
            "if (cmp32(millis(), lastVoltageChangeMs) >= batteryConfig()->vbatDurationForCritical * 100) { voltageState = BATTERY_CRITICAL; }",
        ),
        (
            "src/main/sensors/battery.c",
            "if (voltageMeter.displayFiltered > batteryWarningVoltage) { voltageState = BATTERY_OK; }",
        ),
        (
            "src/main/sensors/battery.c",
            "if (voltageMeter.displayFiltered > batteryCriticalVoltage) { voltageState = BATTERY_WARNING; lastVoltageChangeMs = millis(); }",
        ),
        (
            "src/main/sensors/voltage.c",
            "if (pidProfiles(i)->vbat_sag_compensation > 0 && !RPM_LIMIT_ACTIVE) { sagCompensationConfigured = true; }",
        ),
    ],
    "MSP meanings (firmware/msp_layouts.py)": [
        # Mode ranges: permanent box IDs, aux channel indices, and range steps of 25 us from CHANNEL_RANGE_MIN
        (
            "src/main/msp/msp.c",
            "const box_t *box = findBoxByBoxId(mac->modeId); sbufWriteU8(dst, box->permanentId); "
            "sbufWriteU8(dst, mac->auxChannelIndex); sbufWriteU8(dst, mac->range.startStep); "
            "sbufWriteU8(dst, mac->range.endStep);",
        ),
        (
            "src/main/msp/msp.c",
            "const box_t *box = findBoxByPermanentId(i); if (box) { mac->modeId = box->boxId; "
            "mac->auxChannelIndex = sbufReadU8(src); mac->range.startStep = sbufReadU8(src); "
            "mac->range.endStep = sbufReadU8(src);",
        ),
        ("src/main/msp/msp_box.c", "sbufWriteU8(dst, box->permanentId);"),
        ("src/main/fc/rc_modes.h", "#define MODE_STEP_TO_CHANNEL_VALUE(step) (CHANNEL_RANGE_MIN + 25 * step)"),
        ("src/main/rx/rx.h", "#define MAX_AUX_CHANNEL_COUNT (MAX_SUPPORTED_RC_CHANNEL_COUNT - NON_AUX_CHANNEL_COUNT)"),
        # Channels in rcData order (rc_alias_e)
        (
            "src/main/msp/msp.c",
            "case MSP_RC: for (int i = 0; i < rxRuntimeState.channelCount; i++) { sbufWriteU16(dst, rcData[i]); }",
        ),
        # Attitude: roll and pitch in 0.1 deg, yaw converted to deg
        (
            "src/main/msp/msp.c",
            "case MSP_ATTITUDE: sbufWriteU16(dst, attitude.values.roll); sbufWriteU16(dst, attitude.values.pitch); "
            "sbufWriteU16(dst, DECIDEGREES_TO_DEGREES(attitude.values.yaw));",
        ),
        (
            RAW + "src/main/flight/imu.h",
            (
                "// absolute angle inclination in multiple of 0.1 degree eg attitude.values.yaw 180 deg = 1800",
                "// absolute angle inclination in multiple of 0.1 degree 180 deg = 1800",  # 4.5
            ),
        ),
        ("src/main/common/maths.h", "#define DECIDEGREES_TO_DEGREES(angle) ((angle) / 10)"),
        # Battery: legacy voltage in 0.1 V, voltage in 0.01 V, current in 0.01 A, drawn in mAh
        (
            RAW + "src/main/msp/msp.c",
            "case MSP_ANALOG: sbufWriteU8(dst, (uint8_t)constrain(getLegacyBatteryVoltage(), 0, 255)); "
            "sbufWriteU16(dst, (uint16_t)constrain(getMAhDrawn(), 0, 0xFFFF)); // milliamp hours drawn from battery "
            "sbufWriteU16(dst, getRssi()); "
            "sbufWriteU16(dst, (int16_t)constrain(getAmperage(), -0x8000, 0x7FFF)); // send current in 0.01 A steps, range is -320A to 320A "
            "sbufWriteU16(dst, getBatteryVoltage()); break;",
        ),
        ("src/main/sensors/battery.c", "uint16_t getBatteryVoltage(void) { return voltageMeter.displayFiltered; }"),
        (
            "src/main/sensors/battery.c",
            "uint16_t getLegacyBatteryVoltage(void) { return (voltageMeter.displayFiltered + 5) / 10; }",
        ),
        ("src/main/sensors/battery.c", "int32_t getAmperage(void) { return currentMeter.amperage; }"),
        (RAW + "src/main/sensors/voltage.h", "uint16_t displayFiltered; // voltage in 0.01V steps"),
        (
            RAW + "src/main/sensors/current.h",
            "typedef struct currentMeter_s { int32_t amperage; // current read by current sensor in centiampere (1/100th A)",
        ),
        # VTX: frequencies in MHz, low_power_disarm is the vtx_low_power_disarm setting
        (RAW + "src/main/io/vtx.h", "uint16_t freq; // sets freq in MHz if band=0"),
        ("src/main/msp/msp.c", "sbufWriteU8(dst, vtxSettingsConfig()->lowPowerDisarm);"),
        (
            "src/main/cli/settings.c",
            '{ "vtx_low_power_disarm", VAR_UINT8 | MASTER_VALUE | MODE_LOOKUP, .config.lookup = { TABLE_VTX_LOW_POWER_DISARM }, PG_VTX_SETTINGS_CONFIG, offsetof(vtxSettingsConfig_t, lowPowerDisarm) },',
        ),
        ("src/main/cli/settings.c", "LOOKUP_TABLE_ENTRY(lookupTableVtxLowPowerDisarm),"),
        # Dataflash summary flags (mspFlashFsFlags_e)
        (
            "src/main/msp/msp.c",
            "uint8_t flags = MSP_FLASHFS_FLAG_SUPPORTED; flags |= (flashfsIsReady() ? MSP_FLASHFS_FLAG_READY : 0);",
        ),
    ],
}
