"""Options shared by several commands, reading command input from arguments or stdin, and progress display."""

import sys
import warnings
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from cyclopts import Group, Parameter

from fpv_tools.firmware import supported
from fpv_tools.settings import ConfigExport, LiveFc, Settings, Source

if TYPE_CHECKING:
    from fpv_tools.fc.link import UsbIdentity

Port = Annotated[
    str | None,
    Parameter(
        alias="-p",
        metavar="PORT",
        help="Serial port of the flight controller. Default: the one USB serial port with Betaflight's USB IDs.",
    ),
]
Timeout = Annotated[float, Parameter(metavar="SECONDS", help="Seconds to wait for a reply.")]


IGNORE_VERSION = f"Read logs from firmware releases other than {', '.join(supported())} anyway."

SETTINGS = Group(
    "FC settings",
    help="Some signals need FC settings the log does not record (`fpv bb --help` marks them).",
    sort_key=1,
)


@Parameter(name="*")
@dataclass(frozen=True)
class LogOptions:
    """How logs are read: the FC settings they do not record, and the firmware release check."""

    config: Annotated[Path | None, Parameter(allow_leading_hyphen=True, metavar="FILE", group=SETTINGS)] = None
    "From a CLI export (`dump all` or `diff all`), or - for stdin."

    config_from_fc: Annotated[bool, Parameter(group=SETTINGS)] = False
    """From the flight controller, if a signal needs them. Read through its CLI, after which it cannot arm until it
    reboots."""

    set: Annotated[tuple[str, ...], Parameter(name="--set", metavar="KEY=VALUE", group=SETTINGS)] = ()
    "One setting; overrides the other sources. Repeatable."

    ignore_version: Annotated[bool, Parameter(help=IGNORE_VERSION)] = False

    port: Annotated[
        Port,
        Parameter(
            group=SETTINGS,
            help="Serial port for --config-from-fc. Default: the one USB serial port with Betaflight's USB IDs.",
        ),
    ] = None

    def settings(self) -> Settings:
        if self.config and self.config_from_fc:
            raise ValueError("--config and --config-from-fc exclude each other")
        if self.port is not None and not self.config_from_fc:
            raise ValueError("--port only applies with --config-from-fc")
        source: Source | None = None
        if self.config_from_fc:
            source = LiveFc(self.port)
        elif self.config == Path("-"):
            source = ConfigExport.parse(sys.stdin.read(), "stdin")
        elif self.config:
            source = ConfigExport.read(self.config.expanduser())
        return Settings(Settings.parse_overrides(list(self.set)), source)


def input_lines(arguments: Sequence[str]) -> Iterator[str]:
    """Arguments if any were given, else non-empty, non-comment lines from stdin (when it is not a terminal)."""
    if arguments:
        yield from arguments
        return
    if sys.stdin.isatty():
        raise ValueError("nothing to do: give commands as arguments or on stdin")
    for line in sys.stdin:
        line = line.strip()
        if line and not line.startswith("#"):
            yield line


def confirm(question: str) -> bool:
    """Ask on the terminal, defaulting to no. Without a terminal there is no one to ask: --yes is the way on."""
    if not sys.stdin.isatty():
        raise ValueError(f"{question} (no terminal to ask on: pass --yes to go ahead)")
    print(f"{question} [y/N] ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def await_reboot(identity: UsbIdentity | None) -> None:
    """Return once a rebooting FC is back on USB, so that the next command finds it. Ports that are not USB cannot
    be watched."""
    from fpv_tools.fc.link import BACK_S, GONE_S, await_return

    if identity is None:
        return
    with waiting("waiting for the flight controller to come back"):
        back = await_return(identity)
    if back is None:
        warnings.warn(f"the flight controller did not reboot (it stayed on USB for {GONE_S:g} s)", stacklevel=1)
    elif not back:
        warnings.warn(f"the flight controller left USB and was not back within {BACK_S:g} s", stacklevel=1)


@contextmanager
def transfer(description: str) -> Generator[Callable[[int, int], None]]:
    """A progress bar for a transfer on stderr, while it is a terminal; yields update(done_bytes, total_bytes)."""
    from rich.console import Console
    from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

    console = Console(stderr=True)
    columns = (description, BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn())
    with Progress(*columns, console=console, transient=True, disable=not console.is_terminal) as bar:
        task = bar.add_task(description, total=None)
        yield lambda done, total: bar.update(task, completed=done, total=total)


@contextmanager
def waiting(description: str) -> Generator[None]:
    """A spinner with the time elapsed on stderr, while it is a terminal."""
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn

    console = Console(stderr=True)
    columns = (SpinnerColumn(), description, TimeElapsedColumn())
    with Progress(*columns, console=console, transient=True, disable=not console.is_terminal) as bar:
        bar.add_task(description, total=None)
        yield
