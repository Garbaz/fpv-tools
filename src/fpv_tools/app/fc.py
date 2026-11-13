"""`fpv-tools cli` and `fpv-tools msp`: the flight controller's own interfaces."""

import contextlib
import shlex
import sys
import time
import warnings
from typing import Annotated, Any

from cyclopts import Parameter

from fpv_tools.app.common import Port, Timeout, await_reboot, confirm, input_lines
from fpv_tools.fc.console import ERROR_MARK, NO_REBOOT, End, Session
from fpv_tools.fc.link import open_port, usb_identity
from fpv_tools.fc.msp import MspClient, MspError, MspTimeout, code_of, name_of
from fpv_tools.fc.msp_codes import CODES, NAMES
from fpv_tools.firmware import Profile, profile, supported, version_from_msp
from fpv_tools.firmware.msp_effects import Effect, Kind, effect
from fpv_tools.output import write_json


class CommandFailed(Exception):
    pass


def cli(
    *commands: str,
    save: bool = False,
    no_reboot: bool = False,
    keep_going: Annotated[bool, Parameter(alias="-k")] = False,
    port: Port = None,
) -> None:
    """Run commands in the flight controller's CLI and print their output.

    Commands come from the arguments, or one per line from stdin. The output is what the FC prints, without
    echoes and prompts. Afterwards the FC reboots, dropping changes not saved, unless --save or --no-reboot. A
    `diff all` export ends with `save`, so feeding it back in restores it:

        fpv-tools cli 'diff all' > config.txt
        fpv-tools cli < config.txt
        fpv-tools cli 'set vbat_warning_cell_voltage = 350' --save

    Parameters
    ----------
    commands
        CLI commands, e.g. 'get vbat' 'diff all'.
    save
        Save the settings at the end (the FC reboots).
    no_reboot
        Leave without rebooting: unsaved changes stay active until power-off, and the FC cannot arm until it
        reboots (the firmware blocks arming after a CLI session).
    keep_going
        Run the remaining commands after one fails; --save then does not apply.
    """
    if save and no_reboot:
        raise ValueError("--save and --no-reboot exclude each other")
    lines = list(input_lines(commands))
    end = End.SAVE if save else End.KEEP if no_reboot else End.REBOOT
    failed: list[str] = []
    closing = output = ""
    with open_port(port) as serial, Session(serial, timeout=10.0) as session:
        identity = usb_identity(serial.port)
        for n, command in enumerate(lines, 1):
            output = session.run(command)
            if output:
                print(output, flush=True)
            if ERROR_MARK in output:
                failed.append(command)
                if not keep_going:
                    break
            if not session.active:
                if n < len(lines):
                    raise CommandFailed(
                        f"the FC left the CLI at {command!r}; {len(lines) - n} line(s) after it not run"
                    )
                if save or no_reboot:
                    flag = "--save" if save else "--no-reboot"
                    warnings.warn(f"the FC left the CLI at {command!r}; {flag} did not apply", stacklevel=1)
                break
        if session.active:
            closing = session.close(End.REBOOT if failed and end is End.SAVE else end)
            rebooting = end is not End.KEEP
        else:  # a command left the CLI (save, exit, bl, ...): the FC reboots, unless it was `exit noreboot`
            rebooting = not output.rstrip().endswith(NO_REBOOT)
    if closing:
        print(closing, file=sys.stderr)
    if rebooting:
        await_reboot(identity)
    if failed:
        unsaved = " (--save not applied)" if end is End.SAVE else ""
        raise CommandFailed(f"{len(failed)} command(s) failed{unsaved}: {'; '.join(failed)}")


class _Layouts:
    """Named layouts from the firmware profile, fetched (MSP_FC_VERSION) only when a named layout is needed."""

    def __init__(self, fc: MspClient, ignore_version: bool) -> None:
        self.fc, self.ignore_version = fc, ignore_version
        self._profile: Profile | None = None

    def profile(self) -> Profile:
        if self._profile is None:
            version = version_from_msp(self.fc.request(CODES["MSP_FC_VERSION"]))
            self._profile = profile(version, source="flight controller", ignore_mismatch=self.ignore_version)
        return self._profile


def msp(
    message: str | None = None,
    /,
    *fields: str,
    struct: Annotated[str | None, Parameter(name="--struct", metavar="FORMAT")] = None,
    reply: Annotated[str | None, Parameter(metavar="FORMAT")] = None,
    raw: bool = False,
    as_hex: Annotated[bool, Parameter(name="--hex")] = False,
    every: Annotated[float | None, Parameter(metavar="SECONDS")] = None,
    changes: bool = False,
    count: Annotated[int | None, Parameter(alias="-n", metavar="N")] = None,
    json: bool = False,
    save: bool = False,
    no_reboot: bool = False,
    yes: Annotated[bool, Parameter(alias="-y")] = False,
    ignore_version: Annotated[
        bool,
        Parameter(help=f"Use the known layouts even if the FC runs a release other than {', '.join(supported())}."),
    ] = False,
    port: Port = None,
    timeout: Timeout = 2.0,
) -> None:
    """Send MSP requests and print the replies.

    MESSAGE is a firmware name (MSP_RC, MSP2_SET_TEXT) or a code (105, 0x3007). Messages with a known layout take
    NAME=VALUE fields and reply with named fields; others take values packed by --struct, and reply in hex or as
    unpacked by --reply. Without MESSAGE, requests are read from stdin, one per line.

    Known fields are what the firmware means by them: names (modes, channels, VTX device types) and units (V, A,
    deg, us, MHz), in replies and fields given alike. A value that is not a whole number of the firmware's steps
    is refused, never rounded. --raw shows and takes the values as sent instead.

        fpv-tools msp MSP_ATTITUDE --every 0.1
        fpv-tools msp MSP2_GET_TEXT type=PILOT_NAME
        fpv-tools msp MSP_MOTOR --reply H
        fpv-tools msp MSP2_SET_TEXT type=CRAFT_NAME text=Whoop --save
        fpv-tools msp MSP_SET_MODE_RANGE index=5 mode=ANGLE aux_channel=AUX2 start=1700 end=2100 --save

    The FC is left as found: afterwards it reboots to undo what the messages changed in RAM (settings, and runtime
    state such as motor outputs or the clock), unless --save or --no-reboot. Messages that cannot be undone (e.g.
    MSP_DATAFLASH_ERASE, MSP_RESET_CONF) ask first. Codes that are not Betaflight messages are sent as they are,
    with none of this.

    Parameters
    ----------
    message
        Message name or code.
    fields
        NAME=VALUE for known layouts, or values for --struct.
    struct
        Python `struct` format of the request payload, e.g. '<BH' (little-endian if no byte order is given).
    reply
        `struct` format for the reply, repeated while bytes remain (e.g. 'H' reads all u16 values).
    raw
        Known fields as the firmware sends them: numbers, before names and units.
    as_hex
        Print replies as hex even when the layout is known.
    every
        Repeat the request every this many seconds, until Ctrl-C or --count.
    changes
        With --every: print only replies that differ from the previous one.
    count
        With --every: stop after this many requests.
    json
        Each reply as a JSON object on its own line.
    save
        Write changed settings to flash at the end. Runtime state (motor outputs, ...) is not a setting: it stays
        until the FC reboots.
    no_reboot
        Keep all changes active without saving or rebooting.
    yes
        Do not ask before messages that cannot be undone.
    """
    if save and no_reboot:
        raise ValueError("--save and --no-reboot exclude each other")
    if raw and as_hex:
        raise ValueError("--raw and --hex exclude each other")
    lines = [message, *fields] if message is not None else None
    requests = [lines] if lines else [shlex.split(line) for line in input_lines(())]
    codes = [code_of(request[0]) for request in requests]
    effects = _check(codes, save=save, no_reboot=no_reboot, every=every)
    for code, e in zip(codes, effects, strict=True):
        if e and e.kind is Kind.IRREVERSIBLE and not yes and not confirm(f"{name_of(code)} {e.note}. Go ahead?"):
            return
    pending: list[str] = []  # messages whose changes are in RAM, in the order sent
    rebooted = False
    with open_port(port, timeout) as serial, MspClient(serial) as fc:
        identity = usb_identity(serial.port)
        layouts = _Layouts(fc, ignore_version)
        try:
            for request, code, e in zip(requests, codes, effects, strict=True):
                payload = _payload(layouts, request[0], request[1:], struct, raw)
                if e and e.kind in (Kind.SETTINGS, Kind.RUNTIME):
                    pending.append(name_of(code))  # before sending: Ctrl-C can come during --every
                try:
                    _exchange(fc, layouts, code, payload, reply, raw, as_hex, json, every, changes, count)
                except (MspTimeout, OSError):
                    if not (e and e.reboots):
                        raise
                    # msp_serial.c reboots once its transmit buffer is empty; over USB, before the host has read it
                if e and e.kind is Kind.SAVES:
                    pending = [n for n in pending if _kind(n) is Kind.RUNTIME]
                rebooted |= bool(e and e.reboots)
        except KeyboardInterrupt:
            pass
        except Exception:
            # As `fpv-tools cli` does after a failed command: --save does not apply
            try:
                if not rebooted and _leave(fc, pending, False, no_reboot) and save:
                    warnings.warn("--save not applied", stacklevel=1)
            except (MspError, OSError) as e:
                warnings.warn(f"could not undo {', '.join(dict.fromkeys(pending))}: {e}", stacklevel=1)
            raise
        if not rebooted:
            rebooted = _leave(fc, pending, save, no_reboot)
    if rebooted:
        await_reboot(identity)


def _kind(name: str) -> Kind | None:
    e = effect(name)
    return e.kind if e else None


def _check(codes: list[int], *, save: bool, no_reboot: bool, every: float | None) -> list[Effect | None]:
    """The effect of each request, refusing combinations that cannot do what they say."""
    names = [name_of(code) for code in codes]
    effects = [effect(name) for name in names]
    for i, (code, name, e) in enumerate(zip(codes, names, effects, strict=True)):
        if code not in NAMES:
            message = f"{name} is not a Betaflight message fpv-tools knows: nothing it changes is undone"
            warnings.warn(message, stacklevel=1)
        if e is None:
            continue
        if e.kind is Kind.PASSTHROUGH:
            raise ValueError(f"{name} {e.kind.value}: fpv-tools msp cannot talk through it")
        if e.reboots and (i < len(codes) - 1 or every is not None):
            raise ValueError(f"{name} reboots the FC: it can only be the last request, sent once")
        if e.reboots and (save or no_reboot):
            raise ValueError(f"{name} reboots the FC: --save and --no-reboot do not apply")
        before = [n for n in names[:i] if _kind(n) is Kind.SETTINGS]
        if e.kind is Kind.SAVES and before and not save:
            raise ValueError(
                f"{name} writes all settings to flash, so it would store the changes of "
                f"{', '.join(dict.fromkeys(before))} before it too: pass --save, or send {name} separately"
            )
    return effects


def _leave(fc: MspClient, pending: list[str], save: bool, no_reboot: bool) -> bool:
    """Undo, store or keep the changes in RAM as asked; True if the FC reboots."""
    pending = list(dict.fromkeys(pending))
    runtime = [n for n in pending if _kind(n) is Kind.RUNTIME]
    if not pending:
        return False
    if save:
        if len(runtime) < len(pending):
            fc.request(CODES["MSP_EEPROM_WRITE"])
            print("settings saved", file=sys.stderr)
        for name in runtime:
            note = f": {_note(name)}" if _note(name) else ""
            warnings.warn(f"{name} is not a setting and stays in effect until reboot{note}", stacklevel=1)
        return False
    if no_reboot:
        for name in runtime:
            if note := _note(name):
                warnings.warn(f"{name}: {note}", stacklevel=1)
        return False
    with contextlib.suppress(MspTimeout, OSError):  # the reply can be lost to the reboot, as above
        fc.request(CODES["MSP_REBOOT"], b"\x00")
    print(f"rebooting to undo {', '.join(pending)} (--save or --no-reboot to keep)", file=sys.stderr)
    return True


def _note(name: str) -> str:
    e = effect(name)
    return e.note if e else ""


def _payload(layouts: _Layouts, name: str, values: list[str], struct: str | None, raw: bool) -> bytes:
    from fpv_tools.fc import codec  # construct takes a while to import: not for --help

    if struct is not None:
        return codec.pack(struct, values)
    if not values:
        return b""
    layout = layouts.profile().msp.get(name)
    if layout is None:
        raise ValueError(f"no known layout for {name}: give the payload with --struct FORMAT VALUES")
    names = [str(sub.name) for sub in layout.request.subcons if sub.name]
    if not names:
        raise ValueError(f"{name} takes no request fields (got {' '.join(values)}; options go before the batch)")
    pairs = dict(v.split("=", 1) for v in values if "=" in v)
    if len(pairs) != len(values):
        raise ValueError(f"fields for {name} are NAME=VALUE with NAME one of {names}")
    return codec.build(layout.request, pairs, None if raw else layout.meaning)


def _exchange(
    fc: MspClient,
    layouts: _Layouts,
    code: int,
    payload: bytes,
    reply_fmt: str | None,
    raw: bool,
    as_hex: bool,
    json: bool,
    every: float | None,
    changes: bool,
    count: int | None,
) -> None:
    name = name_of(code)
    known = name.startswith("MSP") and not as_hex and reply_fmt is None
    layout = layouts.profile().msp.get(name) if known else None
    units = layout.units() if layout and not raw else {}
    start, last, sent = time.monotonic(), None, 0
    while True:
        data = fc.request(code, payload)
        sent += 1
        decoded = _decode(layouts, name, data, reply_fmt, raw, as_hex)
        if not (changes and decoded == last):
            _print(name, decoded, units, time.monotonic() - start if every else None, json)
        last = decoded
        if every is None or (count is not None and sent >= count):
            return
        time.sleep(max(0.0, start + sent * every - time.monotonic()))


def _decode(
    layouts: _Layouts, name: str, data: bytes, reply_fmt: str | None, raw: bool, as_hex: bool
) -> dict[str, Any]:
    from fpv_tools.fc import codec

    if reply_fmt is not None:
        return {"values": codec.unpack(reply_fmt, data)}
    if not as_hex and name.startswith("MSP") and (layout := layouts.profile().msp.get(name)) is not None:
        return codec.parse(layout.reply, data, None if raw else layout.meaning) if data else {}
    return {"hex": data.hex()} if data else {}


def _print(name: str, reply: dict[str, Any], units: dict[str, str], t: float | None, json: bool) -> None:
    """A reply; units (by field name) follow the values, or come as their own object in JSON."""
    if json:
        timing = {"t": round(t, 4)} if t is not None else {}
        write_json({"message": name, **timing, "reply": reply, **({"units": units} if units else {})}, line=True)
        return
    prefix = f"{t:8.3f}  " if t is not None else ""
    if t is not None or len(reply) <= 1:
        body = "  ".join(f"{k}={_text(v, units, units.get(k, ''))}" for k, v in reply.items()) or "(empty reply)"
        print(f"{prefix}{body}", flush=True)
        return
    width = max(len(k) for k in reply)
    for k, v in reply.items():
        print(f"{k.ljust(width)}  {_text(v, units, units.get(k, ''), ' ')}")
    sys.stdout.flush()


def _text(value: Any, units: dict[str, str], unit: str = "", space: str = "") -> str:
    """A value as text, with its unit after it (after a list of values, once)."""
    after = f"{space}{unit}" if unit else ""
    match value:
        case list():
            items: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
            if any(isinstance(v, dict) for v in items):
                return "; ".join(_text(v, units) for v in items)
            return " ".join(_text(v, units) for v in items) + after
        case dict():
            fields: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
            return ",".join(f"{k}={_text(v, units, units.get(k, ''))}" for k, v in fields.items())
        case _:
            return f"{value}{after}"


__all__ = ["CommandFailed", "MspError", "cli", "msp"]
