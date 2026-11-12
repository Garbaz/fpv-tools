from pathlib import Path

import numpy as np
import pytest
import reference

from fakes import flight
from fpv_tools.blackbox import bbl, expr, fcmodel, probes
from fpv_tools.blackbox.decoder import decode_file, load
from fpv_tools.blackbox.flight import Flight, SignalError
from fpv_tools.settings import Settings

LOG = Path(__file__).parent / "data" / "flight.bbl"


@pytest.fixture(scope="module")
def real() -> Flight:
    with pytest.warns(UserWarning, match="pre-release"):
        (f,) = load([LOG], settings=Settings({"rssi_channel": "0"}))
    return f


def test_real_log(real: Flight) -> None:
    assert real.log.stats["corrupt"] == 0 and not real.log.stats["truncated_end"]
    # Betaflight's decoder shows only the ARM (0) and BEEPERON (15) boxes (2026.6 fc/rc_modes.h): no flight mode box,
    # so acro throughout; and ARM going off before the end, so the disarm was by switch (fc/core.h DISARM_REASON_*)
    boxes = reference.decoded()["flightModeFlags"].astype(int)
    armed = boxes & 1 << 0
    assert not (boxes & ~(1 << 0 | 1 << 15)).any() and armed.any() and not armed[-1]
    assert set(real["mode"][1:]) == {"ACRO"}
    assert [real.profile.disarm_reasons[e["reason"]] for e in real.log_events if e["event"] == "disarm"] == ["SWITCH"]
    # The first frame is an I-frame written before any slow frame (blackboxLogIteration): its flags are unknown
    assert np.isnan(real["rx"][0]) and real["mode"][0] == "" and real["rx"][1] == 1
    assert real["thr"].min() >= 0 and real["thr"].max() <= 100
    assert real["motor.min"].min() >= 0 and real["motor.max"].max() <= 100
    assert real["acc"][:50].mean() == pytest.approx(1.0, abs=0.1)  # sitting still before takeoff


def test_cached_logs_are_what_the_decoder_produced() -> None:
    (direct,) = [bbl.decode(raw) for raw in bbl.split(LOG.read_bytes())]
    decode_file(LOG)  # caches it, if no earlier test has
    (cached,) = decode_file(LOG)
    assert cached.headers == direct.headers and cached.events == direct.events and cached.stats == direct.stats
    for group in ("main", "slow", "gps"):
        ours, theirs = getattr(cached, group), getattr(direct, group)
        assert ours.keys() == theirs.keys()
        for name in ours:
            assert ours[name].dtype == theirs[name].dtype
            assert np.array_equal(ours[name], theirs[name], equal_nan=True)


def test_dbm_inverts_the_firmware_mapping_exactly() -> None:
    # Where the FC's rssi smoothing has settled, the logged value is the mapping of a whole dBm
    dbm = np.arange(-130, 1)
    rssi = (1023 * (dbm + 130)) // 130  # handleCrsfLinkStatisticsFrame: integer scaleRange
    settings = {"rssi_channel": "0", "rssi_invert": "OFF", "rssi_scale": "100", "rssi_offset": "0"}
    assert np.array_equal(flight({"rssi": rssi}, settings=settings)["dbm"], dbm)
    with pytest.raises(SignalError, match="rescaled"):  # getRssi() applied rssi_scale (rx/rx.c)
        flight({"rssi": rssi}, settings=settings | {"rssi_scale": "90"})["dbm"]


def test_expressions_are_evaluated_not_executed() -> None:
    f = flight({"setpoint[3]": np.array([0, 100, 600]), "gyroADC[0]": np.array([0, -50, 60])})
    assert expr.mask(f, "thr > 5 and abs(gyro.roll) > 40").tolist() == [False, True, True]
    with pytest.raises(expr.ExprError):
        expr.mask(f, "__import__('os')")
    with pytest.raises(SignalError):
        expr.mask(f, "thr.__class__")


def test_high_resolution_logs_are_scaled() -> None:
    # blackbox.c loadMainState scales gyroADC, gyroUnfilt, rcCommand and setpoint by 10
    f = flight({"gyroADC[0]": 1234, "rcCommand[1]": -4321}, headers={"blackbox_high_resolution": "1"})
    assert (f["gyro.roll"][0], f["rc.pitch"][0]) == (123.4, -432.1)


def test_battery_warning_timing() -> None:
    # 1S at 3.70 V, then 3.55 V from t = 2 s; warning level 3.60 V (header), vbat_hysteresis 0.01 V,
    # vbat_display_lpf_period 3 s, vbat_duration_for_warning 1 s. Worked out from the firmware:
    # - voltage task at 50 Hz: pt1 gain k = w / (w + 1), w = 2 pi (1 / 3 s) (1 / 50 s) (pt1FilterGain);
    #   displayFiltered = uint16(355 + 15 (1 - k)^n) after n runs, which is <= 359 once (1 - k)^n < 1/3
    # - alerts task at 5 Hz: the timer counts from its last run that saw > 359, at most 0.2 s before the
    #   crossing, so the warning comes 0.8 to 1.0 s after it
    w = 2 * np.pi / 3 / 50
    runs = np.ceil(np.log(1 / 3) / np.log(1 - w / (w + 1)))  # 27
    crossing = 2.0 + (runs - 1) / 50
    vbat = np.r_[np.full(2000, 370), np.full(4000, 355)]  # vbatLatest at 1 kHz
    settings = {
        "force_battery_cell_count": "1",
        "vbat_hysteresis": "1",
        "vbat_display_lpf_period": "30",
        "vbat_duration_for_warning": "10",
        "vbat_duration_for_critical": "0",
    }
    f = flight({"vbatLatest": vbat}, settings=settings)
    assert f.t[np.argmax(fcmodel.display_voltage(f) <= 3.59)] == pytest.approx(crossing, abs=1e-3)
    warning = f.t[np.argmax(fcmodel.battery_states(f) == 1)]
    assert crossing + 0.8 < warning <= crossing + 1.0 + 1e-3


def test_changes_while_the_selection_is_off_show_where_it_resumes() -> None:
    f = flight({"flightModeFlags": np.array([0, 0, 2, 2, 2])})  # ACRO, then ANGLE (box 1)
    selected = probes.Part(f, np.array([True, True, False, True, True]))
    changes = [(c["from"], c["to"], c["t"]) for c in probes.changes([selected], "mode")]
    assert changes == [(None, "ACRO", f.t[0]), ("ACRO", "ANGLE", f.t[3])]


def test_spectrum_of_a_tone_over_separate_stretches() -> None:
    # A 120 Hz tone of amplitude 100 in two stretches at different offsets; joined, the offset step would leak
    # above the tone (rms 2.6 above 200 Hz instead of the rounding noise's 0.25)
    t = np.arange(6000) / 1000
    x = 100 * np.sin(2 * np.pi * 120 * t) + np.where(t < 3.05, 0, 500)
    selected = np.ones(len(t), dtype=bool)
    selected[3000:3100] = False
    part = probes.Part(flight({"gyroADC[0]": x}), selected)
    result = probes.spectrum([part], "gyro.roll", bands=[(20, None), (200, None)])
    (peak,) = (p for p in result["peaks"] if p["rms"] > 10)
    assert peak["hz"] == pytest.approx(120, abs=result["resolution_hz"])
    assert peak["rms"] == pytest.approx(100 / np.sqrt(2), rel=0.02)
    assert result["bands"][0]["rms"] == pytest.approx(100 / np.sqrt(2), rel=0.02)
    assert result["bands"][1]["rms"] < 1


def test_step_response_of_a_known_system() -> None:
    # Gyro follows setpoint after 5 ms through a first-order lag of 15 ms: 50 % at 5 + 15 ln 2 ms, 90 % at
    # 5 + 15 ln 10 ms
    rng = np.random.default_rng(1)
    setpoint = np.repeat(rng.uniform(-300, 300, 100), 200)
    gain = 1 - np.exp(-1 / 15)
    gyro, y = np.zeros(len(setpoint)), 0.0
    for i, v in enumerate(np.r_[np.zeros(5), setpoint[:-5]]):
        y += gain * (v - y)
        gyro[i] = y
    f = flight({"setpoint[0]": setpoint, "gyroADC[0]": gyro})
    (result,) = probes.step_response([probes.Part(f, np.ones(len(f), dtype=bool))], "gyro.roll", "setpoint.roll")
    assert result["steady_gain"] == pytest.approx(1, abs=0.05)
    assert result["t50_ms"] == pytest.approx(5 + 15 * np.log(2), abs=1.5)
    assert result["t90_ms"] == pytest.approx(5 + 15 * np.log(10), rel=0.1)
