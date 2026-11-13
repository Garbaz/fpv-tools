"""A session with Betaflight's text CLI (cli/cli.c) over the serial port.

Entering: '#'. Each command's output ends with the prompt "\\r\\n# " (cliPrompt). Leaving:
`exit` reboots and drops unsaved changes, `exit noreboot` keeps the FC running with changes in RAM (and arming
blocked: cliEnter sets ARMING_DISABLED_CLI, which only a reboot clears),
`save` writes the settings and reboots.

Commands are sent as given. Whether one ended the session is told from the FC's answer, not from the command: it
prints "Rebooting" (cliRebootEx) or "# leaving CLI mode, ..." (cliExitCmd) instead of a prompt, or drops off USB.
"""

import contextlib
import time
from enum import StrEnum
from typing import Self

from fpv_tools.fc.msp import Stream

PROMPT = b"\r\n# "
ERROR_MARK = "###ERROR"
# The last output line when a command left the CLI instead of returning to the prompt
LEFT_CLI = (b"\r\nRebooting", b"\r\n# leaving CLI mode")
NO_REBOOT = "leaving CLI mode, no reboot"  # cliExitCmd for `exit noreboot`


class ConsoleError(Exception):
    pass


class End(StrEnum):
    """How to leave the CLI."""

    REBOOT = "reboot"  # `exit`: reboot, unsaved changes are dropped (clean state)
    SAVE = "save"  # `save`: write settings, reboot
    KEEP = "keep"  # `exit noreboot`: no reboot; unsaved changes stay active, arming stays blocked (cliEnter)


class Session:
    """A CLI session. The stream should be a pyserial port: its read timeout is lowered to poll for output."""

    poll_s = 0.05  # read granularity
    quiet_s = 0.25  # silence after a prompt that marks the end of a command's output

    def __init__(self, stream: Stream, timeout: float = 5.0) -> None:
        self.stream = stream
        self.timeout = timeout  # longest gap without output before giving up
        self.active = False

    def __enter__(self) -> Self:
        if hasattr(self.stream, "timeout"):
            setattr(self.stream, "timeout", self.poll_s)  # noqa: B010 - pyserial attribute, not in the protocol
        self._drain()
        # From MSP mode, '#' enters the CLI: banner, then prompt. If the FC is still in the CLI (a session that was
        # cut off), '#' is only echoed as the start of a comment line; an empty line then completes it with a prompt.
        try:
            self.stream.write(b"#")
            _, prompt = self._read_until_prompt(self.timeout, echo_only=b"#")
            if not prompt:
                self.stream.write(b"\r\n")
                _, prompt = self._read_until_prompt(self.timeout)
            if not prompt:
                raise ConsoleError("could not enter the CLI")
        except ConsoleError as e:
            self._leave_unconfirmed()
            raise ConsoleError(f"{e}; sent `exit` in case the FC entered it anyway (it reboots if so)") from e
        self.active = True
        return self

    def _leave_unconfirmed(self) -> None:
        """Leave a CLI that '#' may have entered without the prompt being seen (e.g. another program on the port
        read it), so the FC is not left in the CLI, deaf to MSP. If the FC is in MSP mode instead, these bytes do
        nothing: outside a frame it acts only on '#', 0x02 and the reboot character, and a pending reboot is
        cancelled by any later byte, here the final newline (msp_serial.c, anchored)."""
        with contextlib.suppress(OSError):
            self.stream.write(b"exit\r\n")

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        # Never save on an error; otherwise the caller has already closed with its choice
        if self.active:
            self.close(End.REBOOT if exc_type else End.KEEP)

    def _drain(self) -> None:
        """Read away output left over from an earlier session (an FC blocks while its USB buffer is full)."""
        quiet_since = time.monotonic()
        while time.monotonic() - quiet_since < self.quiet_s:
            if self.stream.read(4096):
                quiet_since = time.monotonic()

    def _read_until_prompt(self, timeout: float, echo_only: bytes | None = None) -> tuple[bytes, bool]:
        """Output up to the prompt, and True. Output can contain the prompt's characters ("\\r\\n# " starts comment
        lines in `dump`), so the end is the prompt followed by silence. False when it ends otherwise: with only
        `echo_only` echoed back, or with the FC leaving the CLI (LEFT_CLI, or dropping off USB)."""
        buffer = b""
        last_data = time.monotonic()
        while True:
            try:
                chunk = self.stream.read(4096)
            except OSError:  # the FC rebooted and dropped off USB
                return buffer, False
            now = time.monotonic()
            if chunk:
                buffer += chunk
                last_data = now
            elif now - last_data < self.quiet_s:
                continue
            elif buffer.endswith(PROMPT):
                return buffer[: -len(PROMPT)], True
            elif buffer == echo_only or _last_line(buffer).startswith(LEFT_CLI):
                return buffer, False
            elif now - last_data > timeout:
                raise ConsoleError(f"no CLI prompt within {timeout:g}s; last output {buffer[-200:]!r}")

    def run(self, command: str, timeout: float | None = None) -> str:
        """Run one command as given; returns its output without the echoed command line. If the command left the
        CLI (save, exit, bl, msc), the session is no longer active afterwards."""
        if not self.active:
            raise ConsoleError("CLI session is closed")
        self.stream.write(command.encode() + b"\r\n")
        output, self.active = self._read_until_prompt(timeout or self.timeout)
        lines = output.decode(errors="replace").replace("\r\n", "\n").split("\n")
        if lines and lines[0].strip() == command.strip():
            lines = lines[1:]
        return "\n".join(lines).strip("\n")

    def close(self, end: End) -> str:
        """Leave the CLI; returns what the FC printed on the way out."""
        self.active = False
        self.stream.write({End.REBOOT: b"exit\r\n", End.SAVE: b"save\r\n", End.KEEP: b"exit noreboot\r\n"}[end])
        out = b""
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                chunk = self.stream.read(256)
            except OSError:
                break  # the FC rebooted and dropped off USB
            if not chunk and out:
                break
            out += chunk
        return out.decode(errors="replace").strip()


def _last_line(output: bytes) -> bytes:
    return b"\r\n" + output.rstrip().rpartition(b"\r\n")[2]
