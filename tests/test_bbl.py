"""The decoder against the firmware's encoder semantics (src/main/blackbox/blackbox.c, blackbox_encoding.c)."""

from pathlib import Path

import numpy as np
import reference

from fpv_tools.blackbox import bbl

LOG = Path(__file__).parent / "data" / "flight.bbl"


def encode_tag8_4s16(values: list[int]) -> bytes:
    """blackboxWriteTag8_4S16, transcribed from blackbox_encoding.c."""
    selector = 0
    for v in reversed(values):
        selector <<= 2
        selector |= 0 if v == 0 else 1 if -8 <= v < 8 else 2 if -128 <= v < 128 else 3
    out = [selector]
    nibble, buffer = False, 0
    for v in values:
        match selector & 3:
            case 1:
                if not nibble:
                    buffer, nibble = (v << 4) & 0xFF, True
                else:
                    out.append(buffer | (v & 0x0F))
                    nibble = False
            case 2:
                if not nibble:
                    out.append(v & 0xFF)
                else:
                    out.append(buffer | ((v >> 4) & 0x0F))
                    buffer = (v << 4) & 0xFF
            case 3:
                if not nibble:
                    out += [(v >> 8) & 0xFF, v & 0xFF]
                else:
                    out += [buffer | ((v >> 12) & 0x0F), (v >> 4) & 0xFF]
                    buffer = (v << 4) & 0xFF
            case _:
                pass
        selector >>= 2
    if nibble:
        out.append(buffer)
    return bytes(out)


def test_tag8_4s16_nibble_packing() -> None:
    # Odd nibble counts shift every following value by half a byte: the subtle part of this encoding
    for values in ([3, -100, 1000, 0], [-8, 7, -128, 127], [5, 0, -32768, 32767], [0, 0, 0, 0], [1, 2, 3, -4]):
        reader = bbl.Reader(encode_tag8_4s16(values))
        assert reader.tag8_4s16() == values
        assert reader.pos == len(reader.data)


def test_average_predictor_truncates_like_c() -> None:
    # blackboxWriteMainStateArrayUsingAveragePredictor: (prev1 + prev2) / 2 in C rounds toward zero, not down.
    # A log with fields loopIteration, time, x: an I-frame (x = -3), a P-frame bringing x to 0, then a P-frame
    # whose prediction is (0 + -3) / 2 = -1 in C (Python's -3 // 2 would give -2) and whose delta is 0.
    header = "".join(
        f"H {k}:{v}\n"
        for k, v in {
            "Product": "Blackbox flight data recorder by Nicholas Sherlock",
            "Data version": "2",
            "I interval": "32",
            "P interval": "1",
            "Field I name": "loopIteration,time,x",
            "Field I signed": "0,0,1",
            "Field I predictor": "0,0,0",
            "Field I encoding": "1,1,0",
            "Field P predictor": "6,2,3",
            "Field P encoding": "9,0,0",
        }.items()
    ).encode()
    # I: iteration 0, time 1000 (unsigned VB e8 07), x -3 (zigzag 5); P: time delta 0, x delta 3 (zigzag 6); P: 0, 0
    frames = b"I\x00\xe8\x07\x05" + b"P\x00\x06" + b"P\x00\x00"
    log = bbl.decode(header + frames)
    assert log.main["x"].tolist() == [-3, 0, -1]
    assert log.main["loopIteration"].tolist() == [0, 1, 2]


def test_frame_schedule_with_uneven_intervals() -> None:
    # I interval 102, P interval 4 (logging at 1/4 rate at 3.2 kHz): every 4th loop within an I period, then the
    # next I-frame at 102, two loops after 100 (blackboxShouldLogPFrame / blackboxAdvanceIterationTimers)
    assert [bbl.next_logged_iteration(n, 102, 4) for n in (0, 96, 100, 102)] == [4, 100, 102, 106]


def test_log_cut_off_mid_frame() -> None:
    data = LOG.read_bytes()
    cut = data[: data.rindex(b"E\x0f") - 3]  # inside the last frames, before disarm and end-of-log
    log = bbl.decode(cut)
    full = bbl.decode(data)
    assert log.stats["truncated_end"] == 1 and log.stats["corrupt"] == 0
    assert len(log.main["time"]) < len(full.main["time"])
    assert (log.main["gyroADC[0]"] == full.main["gyroADC[0]"][: len(log.main["time"])]).all()


def test_frames_match_betaflights_decoder() -> None:
    log = bbl.decode(LOG.read_bytes())
    ref = reference.decoded()
    at = np.searchsorted(log.main["loopIteration"], ref["loopIteration"])
    assert np.array_equal(log.main["loopIteration"][at], ref["loopIteration"])
    for name in log.main:
        assert np.array_equal(log.main[name][at], ref[name]), name
    # Slow-frame fields: the first slow frame comes with the first P-frame, after the opening I-frame
    # (blackboxLogIteration writes none with I-frames); the reference shows its initial 0 there instead
    for name in log.slow:
        assert np.isnan(log.slow[name][0]) and np.array_equal(log.slow[name][at][1:], ref[name][1:]), name


def test_frames_the_reference_skips_follow_events_it_cannot_read() -> None:
    # Its parser.c reads no FLIGHT_LOG_EVENT_FLIGHTMODE or _DISARM payload (blackbox.c writes both): it loses sync
    # there and resumes at the next I-frame, every `I interval` (102) iterations
    log = bbl.decode(LOG.read_bytes())
    iterations = log.main["loopIteration"]
    missing = sorted(set(iterations.tolist()) - set(reference.decoded()["loopIteration"].astype(int).tolist()))
    unread = [int(iterations[e["frame"] - 1]) for e in log.events if e["event"] in ("flight_mode", "disarm")]
    gaps = {(i + 1, (i // 102 + 1) * 102 - 1) for i in unread if (i + 1) in iterations}
    covered = {i for lo, hi in gaps for i in range(lo, hi + 1)}
    assert set(missing) == covered
