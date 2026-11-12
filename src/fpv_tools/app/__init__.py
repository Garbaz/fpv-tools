"""The `fpv` command."""

import sys
import warnings

from cyclopts import App, Parameter
from rich.console import Console
from rich.text import Text

from fpv_tools.app import bb, fc
from fpv_tools.app.helpformat import HelpFormatter


def _shown_default(value: object) -> str | None:
    """Defaults worth showing in help: not unset ones, off-by-default flags or empty lists."""
    return None if value is None or value is False or value == () else str(value)


app = App(
    name="fpv",
    help="Talk to Betaflight flight controllers and analyse their blackbox logs.",
    help_format="plaintext",
    group_parameters="Options",
    help_formatter=HelpFormatter(),
    # Errors as one line like fpv's own: no box, and wrapped by the terminal rather than at the window width
    error_console=Console(stderr=True, soft_wrap=True),
    error_formatter=lambda e: Text(f"fpv: {e}"),
    # Flags are opt-in: no generated --no-X / --empty-X
    default_parameter=Parameter(negative=(), show_default=_shown_default),
)
app.command(fc.cli, usage="Usage: fpv cli [OPTIONS] [COMMANDS...]")
app.command(fc.msp, usage="Usage: fpv msp [OPTIONS] [MESSAGE [FIELDS...]]")
app.command(bb.app)


def _warning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: object = None,
    line: str | None = None,
) -> None:
    print(f"fpv: warning: {message}", file=sys.stderr)


def _expected_errors() -> tuple[type[Exception], ...]:
    """Errors that are the user's to fix: reported as one line, without a traceback. Imported only once something
    failed, so that commands (and --help) do not import every module up front."""
    from fpv_tools.blackbox.decoder import DecodeError
    from fpv_tools.blackbox.expr import ExprError
    from fpv_tools.blackbox.flight import SignalError
    from fpv_tools.blackbox.headers import MissingHeader
    from fpv_tools.fc.codec import CodecError
    from fpv_tools.fc.console import ConsoleError
    from fpv_tools.fc.dataflash import DataflashError
    from fpv_tools.fc.link import LinkError
    from fpv_tools.fc.msp import MspError
    from fpv_tools.firmware import FirmwareError
    from fpv_tools.settings import SettingMissing

    return (
        fc.CommandFailed,
        DecodeError,
        ExprError,
        SignalError,
        MissingHeader,
        CodecError,
        ConsoleError,
        DataflashError,
        LinkError,
        MspError,
        FirmwareError,
        SettingMissing,
        ValueError,
        OSError,
    )


def main() -> int:
    warnings.showwarning = _warning
    try:
        app(exit_on_error=True)
    except BrokenPipeError:
        return 0  # the reader went away (e.g. `| head`)
    except Exception as e:
        if not isinstance(e, _expected_errors()):
            raise
        print(f"fpv: {e}", file=sys.stderr)
        return 1
    return 0
