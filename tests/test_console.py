import contextlib

import pytest

from fakes import FakeCli
from fpv_tools.app import fc
from fpv_tools.fc.console import End, Session

# A dump has the prompt's characters ("\r\n# ") at the start of its comment lines
DUMP = "# version\r\n# Betaflight / STM32G47X (G473) 2026.6.0-alpha\r\n\r\n# master\r\nset vbat_hysteresis = 1"


def test_output_containing_prompt_characters_is_read_to_the_end() -> None:
    with Session(FakeCli({"dump all": DUMP})) as cli:
        assert cli.run("dump all").endswith("set vbat_hysteresis = 1")
        cli.close(End.KEEP)


def test_entering_when_the_fc_is_still_in_the_cli() -> None:
    # A cut-off session leaves the FC in the CLI: '#' is just echoed, an empty line then gives a prompt
    fake = FakeCli({"get x": "x = 1"}, in_cli=True)
    with Session(fake) as cli:
        assert cli.run("get x") == "x = 1"
        cli.close(End.REBOOT)
    assert fake.written[:2] == ["#", "\r\n"] and fake.written[-1] == "exit\r\n"


def test_save_noreboot_stays_in_the_cli() -> None:
    with Session(FakeCli({"get x": "x = 1"})) as cli:
        assert cli.run("save noreboot") == "# saving"
        assert cli.active and cli.run("get x") == "x = 1"
        cli.close(End.KEEP)


def test_commands_that_leave_the_cli_end_the_session() -> None:
    for command, last in (("save", "Rebooting"), ("exit noreboot", "# leaving CLI mode, no reboot")):
        fake = FakeCli({})
        with Session(fake) as cli:
            assert cli.run(command).endswith(last)
            assert not cli.active
        assert fake.written[-1] == f"{command}\r\n"  # nothing sent after it


def _connect(monkeypatch: pytest.MonkeyPatch, fake: FakeCli) -> FakeCli:
    def open_port(*_: object) -> contextlib.nullcontext[FakeCli]:
        return contextlib.nullcontext(fake)

    monkeypatch.setattr(fc, "open_port", open_port)
    return fake


def test_fpv_cli_sends_save_as_given_and_refuses_lines_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connect(monkeypatch, FakeCli({"set x = 1": ""}))
    fc.cli("set x = 1", "save")
    assert fake.written[-2:] == ["set x = 1\r\n", "save\r\n"]

    _connect(monkeypatch, FakeCli({}))
    with pytest.raises(fc.CommandFailed, match="left the CLI at 'save'; 1 line"):
        fc.cli("save", "get x")


def test_fpv_cli_does_not_save_after_a_failed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connect(monkeypatch, FakeCli({"set x = 1": "", "set y = 2": "###ERROR: set: Invalid name"}))
    with pytest.raises(fc.CommandFailed, match="--save not applied"):
        fc.cli("set x = 1", "set y = 2", "get x", save=True)
    assert "get x\r\n" not in fake.written and "save\r\n" not in fake.written
    assert fake.written[-1] == "exit\r\n"  # the reboot drops the change that did apply
