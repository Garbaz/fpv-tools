"""Decoder for Betaflight blackbox logs (.bbl), written from the firmware's encoder.

Reference: src/main/blackbox/blackbox.c (frame writers, header, frame schedule), blackbox_encoding.c (value
encodings), blackbox_fielddefs.h (predictor and encoding ids) of the supported release lines.

A file holds one or more logs, each a text header ("H name:value" lines) followed by frames, each starting with a
marker byte: I (intra: full main state), P (inter: main state relative to history), S (slow: flags, written when
they change), E (event), G (GPS) and H (GPS home). The header lists, per frame type, each field's name, signedness,
predictor and encoding, and the decoder follows that list.

Corruption: a frame must be followed by another marker or the end of the data; otherwise it is dropped, the decoder
resynchronises at the next marker, and main frames resume only at the next I-frame (P-frames need intact history).
A log that ends without its end-of-log event (power cut, full flash) usually ends in a frame that cannot be told
apart from a cut-off one (erased flash reads 0xFF and can complete it); such a last frame is dropped and counted as
`truncated_end`, not as corruption.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

LOG_START = b"H Product:Blackbox flight data recorder by Nicholas Sherlock\n"
MARKERS = b"IPSEGH"
LOG_END_MESSAGE = b"End of log\x00"

# blackbox_fielddefs.h flightLogFieldPredictor_e
PREDICT_0, PREVIOUS, STRAIGHT_LINE, AVERAGE_2, MINTHROTTLE, MOTOR_0, INC = 0, 1, 2, 3, 4, 5, 6
HOME_COORD, PREDICT_1500, VBATREF, LAST_MAIN_FRAME_TIME, MINMOTOR = 7, 8, 9, 10, 11
# blackbox_fielddefs.h flightLogFieldEncoding_e
SIGNED_VB, UNSIGNED_VB, NEG_14BIT, TAG8_8SVB, TAG2_3S32, TAG8_4S16, NULL = 0, 1, 3, 6, 7, 8, 9
GROUP_SIZE = {TAG8_8SVB: 8, TAG2_3S32: 3, TAG8_4S16: 4}

# blackbox_fielddefs.h FlightLogEvent
EVENT_SYNC_BEEP, EVENT_INFLIGHT_ADJUSTMENT, EVENT_LOGGING_RESUME, EVENT_DISARM = 0, 13, 14, 15
EVENT_FLIGHTMODE, EVENT_LOG_END = 30, 255
INFLIGHT_ADJUSTMENT_FLOAT_FLAG = 128  # FLIGHT_LOG_EVENT_INFLIGHT_ADJUSTMENT_FUNCTION_FLOAT_VALUE_FLAG


class FormatError(Exception):
    pass


class _Corrupt(Exception):
    """A frame did not decode cleanly."""


# --- value decoding (inverse of blackbox_encoding.c) -------------------------------------------------------------


class Reader:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise _Corrupt("end of data inside a frame")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def unsigned_vb(self) -> int:
        """blackboxWriteUnsignedVB: 7 bits per byte, low first, high bit set on all but the last (32-bit values)."""
        result = shift = 0
        for _ in range(5):
            b = self.byte()
            result |= (b & 0x7F) << shift
            if b < 0x80:
                return result
            shift += 7
        raise _Corrupt("variable-byte value longer than 5 bytes")

    def signed_vb(self) -> int:
        """blackboxWriteSignedVB: zigzag (value << 1) ^ (value >> 31), then unsigned VB."""
        u = self.unsigned_vb()
        return (u >> 1) ^ -(u & 1)

    def neg_14bit(self) -> int:
        """NEG_14BIT: the writer stores (prediction - value) & 0x3FFF; returns value - prediction."""
        return -sign_extend(self.unsigned_vb() & 0x3FFF, 14)

    def tag2_3s32(self) -> list[int]:
        """blackboxWriteTag2_3S32: three values, 2/4/6 bits packed or 1-4 bytes each, by the top two bits."""
        lead = self.byte()
        match lead >> 6:
            case 0:
                return [sign_extend((lead >> 4) & 3, 2), sign_extend((lead >> 2) & 3, 2), sign_extend(lead & 3, 2)]
            case 1:
                b = self.byte()
                return [sign_extend(lead & 0x0F, 4), sign_extend(b >> 4, 4), sign_extend(b & 0x0F, 4)]
            case 2:
                return [
                    sign_extend(lead & 0x3F, 6),
                    sign_extend(self.byte() & 0x3F, 6),
                    sign_extend(self.byte() & 0x3F, 6),
                ]
            case _:
                values: list[int] = []
                for x in range(3):
                    size = ((lead >> (2 * x)) & 3) + 1  # first value in the low bits
                    raw = 0
                    for i in range(size):
                        raw |= self.byte() << (8 * i)
                    values.append(sign_extend(raw, 8 * size))
                return values

    def tag8_4s16(self) -> list[int]:
        """blackboxWriteTag8_4S16: a selector byte (2 bits per value, first value lowest) for zero/4/8/16 bits;
        nibble-packed, high nibble first."""
        selector = self.byte()
        values: list[int] = []
        nibble = False  # a byte is buffered, its low nibble not yet used
        buffer = 0
        for x in range(4):
            match (selector >> (2 * x)) & 3:
                case 0:
                    values.append(0)
                case 1:
                    if nibble:
                        values.append(sign_extend(buffer & 0x0F, 4))
                    else:
                        buffer = self.byte()
                        values.append(sign_extend(buffer >> 4, 4))
                    nibble = not nibble
                case 2:
                    if nibble:
                        high = buffer & 0x0F
                        buffer = self.byte()
                        values.append(sign_extend((high << 4) | (buffer >> 4), 8))
                    else:
                        values.append(sign_extend(self.byte(), 8))
                case _:
                    if nibble:
                        high = buffer & 0x0F
                        middle = self.byte()
                        buffer = self.byte()
                        values.append(sign_extend((high << 12) | (middle << 4) | (buffer >> 4), 16))
                    else:
                        high = self.byte()
                        values.append(sign_extend((high << 8) | self.byte(), 16))
        return values

    def tag8_8svb(self, count: int) -> list[int]:
        """blackboxWriteTag8_8SVB: one value is a plain signed VB; more get a header byte flagging the non-zero ones
        (first value in the low bit), followed by those as signed VB."""
        if count == 1:
            return [self.signed_vb()]
        header = self.byte()
        return [self.signed_vb() if header >> i & 1 else 0 for i in range(count)]


def next_logged_iteration(last: int, i_interval: int, p_interval: int) -> int:
    """The loop iteration logged after `last` (blackboxShouldLogIFrame/PFrame with blackboxAdvanceIterationTimers):
    iteration n is logged when n % I_interval == 0 (I-frame) or (n % I_interval) % P_interval == 0 (P-frame)."""
    if p_interval <= 1:
        return last + 1
    n = last + 1
    while (n % i_interval) % p_interval != 0:
        n += 1
    return n


def sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


# --- header and frame definitions ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameDef:
    """Fields of one frame type as the header defines them."""

    names: tuple[str, ...]
    signed: tuple[bool, ...]
    predictors: tuple[int, ...]
    encodings: tuple[int, ...]

    def groups(self) -> list[tuple[int, list[int]]]:
        """Fields grouped as the writer encodes them: consecutive fields of a tagged encoding share one group, up
        to its size (8, 3 or 4)."""
        out: list[tuple[int, list[int]]] = []
        i = 0
        while i < len(self.names):
            encoding = self.encodings[i]
            size = GROUP_SIZE.get(encoding, 1)
            members = [i]
            while (
                len(members) < size
                and i + len(members) < len(self.names)
                and self.encodings[i + len(members)] == encoding
            ):
                members.append(i + len(members))
            out.append((encoding, members))
            i += len(members)
        return out


def parse_header(data: bytes) -> tuple[dict[str, str], int]:
    """Header lines up to the first frame; returns them and the offset of the first frame."""
    headers: dict[str, str] = {}
    pos = 0
    while data.startswith(b"H ", pos):
        end = data.find(b"\n", pos)
        if end < 0:
            raise FormatError("header line without end")
        name, sep, value = data[pos + 2 : end].decode("latin-1").partition(":")
        if not sep:
            raise FormatError(f"malformed header line {data[pos:end]!r}")
        headers[name] = value
        pos = end + 1
    return headers, pos


def frame_def(headers: dict[str, str], kind: str, names_from: str | None = None) -> FrameDef | None:
    """The definition of frame type `kind` (P shares I's names and signedness)."""
    names_key = f"Field {names_from or kind} name"
    if names_key not in headers:
        return None

    def numbers(key: str, default: int | None = None) -> tuple[int, ...]:
        if key not in headers:
            if default is None:
                raise FormatError(f"header has no {key!r}")
            return (default,) * len(names)
        return tuple(int(v) for v in headers[key].split(","))

    names = tuple(headers[names_key].split(","))
    definition = FrameDef(
        names,
        tuple(bool(s) for s in numbers(f"Field {names_from or kind} signed", 0)),
        numbers(f"Field {kind} predictor"),
        numbers(f"Field {kind} encoding"),
    )
    if not len(definition.names) == len(definition.signed) == len(definition.predictors) == len(definition.encodings):
        raise FormatError(f"field lists of {kind} frames differ in length")
    for encoding in definition.encodings:
        if encoding not in (SIGNED_VB, UNSIGNED_VB, NEG_14BIT, NULL, *GROUP_SIZE):
            raise FormatError(f"unknown field encoding {encoding} in {kind} frames")
    return definition


# --- decoding ---------------------------------------------------------------------------------------------------


@dataclass
class Log:
    """One decoded log: main frames (one row per logged loop iteration), the slow fields as they applied to each
    main frame (NaN where no slow frame had been written yet), GPS frames, events, and decoding statistics."""

    headers: dict[str, str]
    main: dict[str, NDArray[np.int64]]
    slow: dict[str, NDArray[np.float64]]
    gps: dict[str, NDArray[np.int64]]
    gps_home: list[dict[str, int]]
    events: list[dict[str, Any]]
    stats: dict[str, int] = field(default_factory=dict[str, int])


def split(data: bytes) -> list[bytes]:
    """The logs in a file (each starts with the Product header line). A BTFL_ALL file is all logs concatenated."""
    starts = [i for i in _find_all(data, LOG_START)]
    return [data[s:e] for s, e in zip(starts, [*starts[1:], len(data)], strict=True)]


def _find_all(data: bytes, needle: bytes) -> Iterator[int]:
    i = data.find(needle)
    while i >= 0:
        yield i
        i = data.find(needle, i + 1)


def decode(data: bytes) -> Log:
    headers, pos = parse_header(data)
    if headers.get("Data version") != "2":
        raise FormatError(f"unsupported data version {headers.get('Data version')!r}")
    i_def = frame_def(headers, "I")
    p_def = frame_def(headers, "P", names_from="I")
    if i_def is None or p_def is None:
        raise FormatError("header has no main frame fields")
    s_def = frame_def(headers, "S")
    g_def = frame_def(headers, "G")
    h_def = frame_def(headers, "H")
    decoder = _Decoder(headers, i_def, p_def, s_def, g_def, h_def)
    decoder.run(data, pos)
    return decoder.result()


class _Decoder:
    def __init__(
        self,
        headers: dict[str, str],
        i_def: FrameDef,
        p_def: FrameDef,
        s_def: FrameDef | None,
        g_def: FrameDef | None,
        h_def: FrameDef | None,
    ) -> None:
        self.headers = headers
        self.i_def, self.p_def, self.s_def, self.g_def, self.h_def = i_def, p_def, s_def, g_def, h_def
        self.i_groups, self.p_groups = i_def.groups(), p_def.groups()
        self.s_groups = s_def.groups() if s_def else []
        self.g_groups = g_def.groups() if g_def else []
        self.h_groups = h_def.groups() if h_def else []
        names = i_def.names
        self.iteration_index = names.index("loopIteration")
        self.time_index = names.index("time")
        self.motor0_index = names.index("motor[0]") if "motor[0]" in names else None
        motor_low = int(headers.get("motorOutput", "0").split(",")[0])
        self.constants = {
            MINTHROTTLE: int(headers.get("minthrottle", "0")),
            PREDICT_1500: 1500,
            VBATREF: int(headers.get("vbatref", "0")),
            MINMOTOR: motor_low,
        }
        self.i_interval = int(headers["I interval"])
        p_interval = headers.get("P interval", "1")
        if "/" in p_interval:
            raise FormatError(f"P interval {p_interval!r}: logs older than Betaflight 4.x are not supported")
        self.p_interval = int(p_interval)
        # Wrap-around for the stored 32-bit values (blackbox.c keeps uint32/int32/int16 state, int32 arithmetic)
        self.i_masks = [(1 << 32, s) for s in i_def.signed]
        self.rows: list[list[int]] = []
        self.slow_rows: list[list[int] | None] = []
        self.slow: list[int] | None = None
        self.gps_rows: list[list[int]] = []
        self.gps_home: list[int] | None = None
        self.gps_homes: list[dict[str, int]] = []
        self.events: list[dict[str, Any]] = []
        self.prev1: list[int] | None = None  # main history: last and second-last logged frames
        self.prev2: list[int] | None = None
        self.stats = {
            "I": 0,
            "P": 0,
            "S": 0,
            "G": 0,
            "H": 0,
            "E": 0,
            "corrupt": 0,
            "skipped_bytes": 0,
            "truncated_end": 0,
        }
        self.clean_end = False
        self._frames_after_last_corruption = 0

    # values ----------------------------------------------------------------------------------------------------

    def _read_values(self, reader: Reader, groups: list[tuple[int, list[int]]], count: int) -> list[int]:
        raw = [0] * count
        for encoding, members in groups:
            match encoding:
                case 0:  # SIGNED_VB
                    raw[members[0]] = reader.signed_vb()
                case 1:  # UNSIGNED_VB
                    raw[members[0]] = reader.unsigned_vb()
                case 3:  # NEG_14BIT
                    raw[members[0]] = reader.neg_14bit()
                case 9:  # NULL
                    pass
                case 6:  # TAG8_8SVB
                    for i, v in zip(members, reader.tag8_8svb(len(members)), strict=True):
                        raw[i] = v
                case 7:  # TAG2_3S32
                    for i, v in zip(members, reader.tag2_3s32(), strict=False):
                        raw[i] = v
                case _:  # TAG8_4S16
                    for i, v in zip(members, reader.tag8_4s16(), strict=False):
                        raw[i] = v
        return raw

    def _wrap(self, values: list[int], signed: tuple[bool, ...]) -> list[int]:
        """Values as the firmware stores them: 32-bit, signed or unsigned per field."""
        out: list[int] = []
        for v, s in zip(values, signed, strict=True):
            v &= 0xFFFFFFFF
            out.append(v - (1 << 32) if s and v & 0x80000000 else v)
        return out

    # frames ----------------------------------------------------------------------------------------------------

    def _intra(self, reader: Reader) -> list[int]:
        raw = self._read_values(reader, self.i_groups, len(self.i_def.names))
        values = raw[:]
        for i, predictor in enumerate(self.i_def.predictors):
            match predictor:
                case 0:
                    pass
                case 5:  # MOTOR_0: relative to this frame's motor[0], already decoded
                    if self.motor0_index is None:
                        raise FormatError("MOTOR_0 predictor without a motor[0] field")
                    values[i] += values[self.motor0_index]
                case 4 | 8 | 9 | 11:
                    values[i] += self.constants[predictor]
                case _:
                    raise FormatError(f"predictor {predictor} in I-frames")
        return self._wrap(values, self.i_def.signed)

    def _inter(self, reader: Reader, prev1: list[int], prev2: list[int]) -> list[int]:
        raw = self._read_values(reader, self.p_groups, len(self.p_def.names))
        skipped = self._skipped(prev1[self.iteration_index])
        values = raw[:]
        for i, predictor in enumerate(self.p_def.predictors):
            match predictor:
                case 0:
                    pass
                case 1:  # PREVIOUS
                    values[i] += prev1[i]
                case 2:  # STRAIGHT_LINE
                    values[i] += 2 * prev1[i] - prev2[i]
                case 3:  # AVERAGE_2: (prev1 + prev2) / 2 in C, which truncates toward zero
                    total = prev1[i] + prev2[i]
                    values[i] += total // 2 if total >= 0 else -(-total // 2)
                case 6:  # INC: one more than the last logged iteration, plus iterations not logged
                    values[i] += prev1[i] + 1 + skipped
                case 4 | 8 | 9 | 11:
                    values[i] += self.constants[predictor]
                case _:
                    raise FormatError(f"predictor {predictor} in P-frames")
        return self._wrap(values, self.i_def.signed)

    def _skipped(self, last_iteration: int) -> int:
        return next_logged_iteration(last_iteration, self.i_interval, self.p_interval) - last_iteration - 1

    def _slow(self, reader: Reader) -> list[int]:
        assert self.s_def is not None
        raw = self._read_values(reader, self.s_groups, len(self.s_def.names))
        if any(p != 0 for p in self.s_def.predictors):
            raise FormatError("S-frame fields with predictors")
        return self._wrap(raw, self.s_def.signed)

    def _gps_home(self, reader: Reader) -> list[int]:
        assert self.h_def is not None
        raw = self._read_values(reader, self.h_groups, len(self.h_def.names))
        if any(p != 0 for p in self.h_def.predictors):
            raise FormatError("H-frame fields with predictors")
        return self._wrap(raw, self.h_def.signed)

    def _gps(self, reader: Reader) -> list[int]:
        assert self.g_def is not None
        raw = self._read_values(reader, self.g_groups, len(self.g_def.names))
        values = raw[:]
        for i, (name, predictor) in enumerate(zip(self.g_def.names, self.g_def.predictors, strict=True)):
            match predictor:
                case 0:
                    pass
                case 7:  # HOME_COORD: GPS_coord[n] relative to GPS_home[n] (writeGPSFrame)
                    if self.gps_home is None:
                        raise _Corrupt("GPS frame before any home frame")
                    values[i] += self.gps_home[int(name[name.index("[") + 1 : -1])]
                case 10:  # LAST_MAIN_FRAME_TIME
                    if self.prev1 is None:
                        raise _Corrupt("GPS frame without a main frame")
                    values[i] += self.prev1[self.time_index]
                case _:
                    raise FormatError(f"predictor {predictor} in G-frames")
        return self._wrap(values, self.g_def.signed)

    def _event(self, reader: Reader) -> dict[str, Any] | None:
        """blackboxLogEvent. Returns None at the end-of-log marker."""
        kind = reader.byte()
        # Events are written before the main frame of their loop iteration (blackboxLogIteration), so they belong
        # to the next main frame; result() resolves its time
        frame = len(self.rows)
        match kind:
            case 0:
                return {"event": "sync_beep", "frame": frame, "beep_time": reader.unsigned_vb()}
            case 30:
                return {
                    "event": "flight_mode",
                    "frame": frame,
                    "flags": reader.unsigned_vb(),
                    "last_flags": reader.unsigned_vb(),
                }
            case 15:
                return {"event": "disarm", "frame": frame, "reason": reader.unsigned_vb()}
            case 13:
                function = reader.byte()
                if function & INFLIGHT_ADJUSTMENT_FLOAT_FLAG:
                    raw = bytes(reader.byte() for _ in range(4))
                    value: float | int = float(np.frombuffer(raw, "<f4")[0])
                else:
                    value = reader.signed_vb()
                return {"event": "inflight_adjustment", "frame": frame, "function": function & 0x7F, "value": value}
            case 14:
                iteration, resume_time = reader.unsigned_vb(), reader.unsigned_vb()
                return {"event": "logging_resume", "frame": frame, "iteration": iteration, "resume_time": resume_time}
            case 255:
                end = bytes(reader.byte() for _ in range(len(LOG_END_MESSAGE)))
                if end != LOG_END_MESSAGE:
                    raise _Corrupt("bytes after a log-end event are not the end-of-log message")
                return None
            case _:
                raise _Corrupt(f"unknown event {kind}")

    # stream ----------------------------------------------------------------------------------------------------

    def run(self, data: bytes, pos: int) -> None:
        reader = Reader(data, pos)
        handlers: dict[int, Callable[[Reader], bool]] = {
            ord("I"): self._on_intra,
            ord("P"): self._on_inter,
            ord("S"): self._on_slow,
            ord("E"): self._on_event,
            ord("G"): self._on_gps,
            ord("H"): self._on_gps_home,
        }
        while reader.pos < len(data):
            start = reader.pos
            marker = data[start]
            handler = handlers.get(marker)
            if handler is None:
                self._resync(reader, start)
                continue
            reader.pos += 1
            state = self._save()
            self._frames_after_last_corruption += 1
            try:
                keep_going = handler(reader)
                # The end-of-log event is followed by whatever the storage held (erased flash: 0xFF)
                if keep_going and reader.pos < len(data) and data[reader.pos] not in MARKERS:
                    raise _Corrupt("frame not followed by another frame")
            except _Corrupt:
                self._restore(state)
                self.stats["corrupt"] += 1
                self._frames_after_last_corruption = 0
                self.prev1 = self.prev2 = None  # P-frames need intact history: wait for the next I-frame
                self._resync(reader, start)
                continue
            self.stats[chr(marker)] += 1
            if not keep_going:
                self.clean_end = True
                break
        if not self.clean_end and self.stats["corrupt"] and not self._frames_after_last_corruption:
            self.stats["corrupt"] -= 1
            self.stats["truncated_end"] = 1

    def _resync(self, reader: Reader, start: int) -> None:
        next_marker = min(
            (p for p in (reader.data.find(bytes([m]), start + 1) for m in MARKERS) if p >= 0), default=len(reader.data)
        )
        self.stats["skipped_bytes"] += next_marker - start
        reader.pos = next_marker

    def _save(
        self,
    ) -> tuple[int, int, int, int, int, list[int] | None, list[int] | None, list[int] | None, list[int] | None]:
        return (
            len(self.rows),
            len(self.slow_rows),
            len(self.gps_rows),
            len(self.events),
            len(self.gps_homes),
            self.prev1,
            self.prev2,
            self.slow,
            self.gps_home,
        )

    def _restore(
        self,
        state: tuple[int, int, int, int, int, list[int] | None, list[int] | None, list[int] | None, list[int] | None],
    ) -> None:
        rows, slow_rows, gps_rows, events, homes, self.prev1, self.prev2, self.slow, self.gps_home = state
        del (
            self.rows[rows:],
            self.slow_rows[slow_rows:],
            self.gps_rows[gps_rows:],
            self.events[events:],
            self.gps_homes[homes:],
        )

    def _on_intra(self, reader: Reader) -> bool:
        values = self._intra(reader)
        if self.prev1 is not None and values[self.iteration_index] <= self.prev1[self.iteration_index]:
            raise _Corrupt("I-frame iteration does not advance")
        self._main(values)
        self.prev2 = values  # after an I-frame both history slots hold it (blackbox.c writeIntraframe)
        return True

    def _on_inter(self, reader: Reader) -> bool:
        if self.prev1 is None or self.prev2 is None:
            raise _Corrupt("P-frame without history")
        values = self._inter(reader, self.prev1, self.prev2)
        self.prev2 = self.prev1
        self._main(values)
        return True

    def _main(self, values: list[int]) -> None:
        self.rows.append(values)
        self.slow_rows.append(self.slow)
        self.prev1 = values

    def _on_slow(self, reader: Reader) -> bool:
        self.slow = self._slow(reader)
        return True

    def _on_gps_home(self, reader: Reader) -> bool:
        self.gps_home = self._gps_home(reader)
        assert self.h_def is not None
        self.gps_homes.append(dict(zip(self.h_def.names, self.gps_home, strict=True)))
        return True

    def _on_gps(self, reader: Reader) -> bool:
        self.gps_rows.append([*self._gps(reader), len(self.rows) - 1])
        return True

    def _on_event(self, reader: Reader) -> bool:
        event = self._event(reader)
        if event is None:
            self.events.append({"event": "log_end", "frame": len(self.rows)})
            return False
        self.events.append(event)
        return True

    def result(self) -> Log:
        main = np.array(self.rows, dtype=np.int64).reshape(len(self.rows), len(self.i_def.names))
        columns = {name: main[:, i] for i, name in enumerate(self.i_def.names)}
        columns["time"] = _unwrap_time(columns["time"])
        slow: dict[str, NDArray[np.float64]] = {}
        if self.s_def is not None:
            table = np.full((len(self.rows), len(self.s_def.names)), np.nan)
            for r, row in enumerate(self.slow_rows):
                if row is not None:
                    table[r] = row
            slow = {name: table[:, i] for i, name in enumerate(self.s_def.names)}
        gps: dict[str, NDArray[np.int64]] = {}
        if self.g_def is not None:
            names = [*self.g_def.names, "main_frame"]
            gps_table = np.array(self.gps_rows, dtype=np.int64).reshape(len(self.gps_rows), len(names))
            gps = {name: gps_table[:, i] for i, name in enumerate(names)}
        times = columns["time"]
        for event in self.events:  # the time of the frame an event precedes, or of the last frame at the end
            frame = min(event["frame"], len(times) - 1)
            event["time"] = int(times[frame]) if frame >= 0 else None
        return Log(self.headers, columns, slow, gps, self.gps_homes, self.events, self.stats)


def _unwrap_time(time: NDArray[np.int64]) -> NDArray[np.int64]:
    """The FC's time is a 32-bit microsecond counter; undo wrap-arounds (every ~71.6 minutes)."""
    if len(time) < 2:
        return time
    steps = np.diff(time)
    wraps = np.cumsum(np.r_[0, steps < -(1 << 31)]) << 32
    return time + wraps
