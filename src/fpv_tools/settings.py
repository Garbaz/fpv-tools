"""FC settings that logs do not record, from explicit sources only.

Sources, first match wins:
  --set KEY=VALUE        given on the command line
  --config FILE          a CLI export (`diff all` or `dump all`), - for stdin
  --config-from-fc       read from the connected FC on demand (`dump all`, leaving with `exit noreboot`)

There are no built-in defaults: a setting nobody provides is an error that says how to provide it.
A `diff all` export omits settings at their default, so such a setting is missing there too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from fpv_tools.firmware import Version

SECTION_COMMANDS = ("profile", "rateprofile", "battery_profile")


class SettingMissing(Exception):
    pass


class Source(Protocol):
    def get(self, key: str) -> str | None: ...


@dataclass
class ConfigExport:
    """A CLI export. Profile sections (`profile N` ...) are kept apart; the export's last selection of each is the
    active one, which is what lookups use."""

    name: str
    version: Version | None = None
    master: dict[str, str] = field(default_factory=dict[str, str])
    sections: dict[tuple[str, int], dict[str, str]] = field(default_factory=dict[tuple[str, int], dict[str, str]])
    active: dict[str, int] = field(default_factory=dict[str, int])
    features: dict[str, bool] = field(default_factory=dict[str, bool])

    @classmethod
    def read(cls, path: Path) -> ConfigExport:
        return cls.parse(path.read_text(errors="replace"), path.name)

    @classmethod
    def parse(cls, text: str, name: str) -> ConfigExport:
        export = cls(name)
        current = export.master
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# Betaflight /"):  # printVersion(): "# Betaflight / TARGET (ID) 2026.6.0-alpha ..."
                export.version = Version.find(line)
            if not line or line.startswith("#"):
                continue
            command, _, rest = line.partition(" ")
            match command:
                case "set":
                    key, sep, value = rest.partition("=")
                    if sep:
                        current[key.strip()] = value.strip()
                case "feature":
                    name = rest.strip()
                    export.features[name.removeprefix("-")] = not name.startswith("-")
                case _ if command in SECTION_COMMANDS and rest.strip().isdigit():
                    index = int(rest)
                    export.active[command] = index
                    current = export.sections.setdefault((command, index), {})
                case _:
                    pass
        return export

    def get(self, key: str) -> str | None:
        if key in self.master:
            return self.master[key]
        for command, index in self.active.items():
            if key in (section := self.sections.get((command, index), {})):
                return section[key]
        return None


class LiveFc:
    """Settings of the connected FC: one `dump all` on first use, parsed like an export file. The CLI session ends
    with `exit noreboot`: no setting changes and no reboot, but the firmware keeps arming blocked after a CLI
    session until the FC reboots (cliEnter sets ARMING_DISABLED_CLI, nothing clears it)."""

    def __init__(self, port: str | None = None) -> None:
        self.port = port
        self._export: ConfigExport | None = None

    def export(self) -> ConfigExport:
        if self._export is None:
            from fpv_tools.fc.console import End, Session
            from fpv_tools.fc.link import open_port

            with open_port(self.port) as port, Session(port, timeout=30.0) as cli:
                text = cli.run("dump all")
                cli.close(End.KEEP)
            self._export = ConfigExport.parse(text, "flight controller")
        return self._export

    def get(self, key: str) -> str | None:
        return self.export().get(key)


class Settings:
    def __init__(self, overrides: Mapping[str, str] | None = None, source: Source | None = None) -> None:
        self.overrides = dict(overrides or {})
        self.source = source

    def get(self, key: str) -> str | None:
        if key in self.overrides:
            return self.overrides[key]
        return self.source.get(key) if self.source else None

    def require(self, key: str, purpose: str) -> str:
        value = self.get(key)
        if value is None:
            where = (
                f" (not in {self.source.name}: a `diff all` omits defaults)"
                if isinstance(self.source, ConfigExport)
                else ""
            )
            raise SettingMissing(
                f"{purpose} needs the FC setting {key}{where}; "
                f"pass --set {key}=VALUE, --config EXPORT or --config-from-fc"
            )
        return value

    def number(self, key: str, purpose: str) -> int:
        return int(self.require(key, purpose))

    @staticmethod
    def parse_overrides(items: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in items:
            key, sep, value = item.partition("=")
            if not sep or not key.strip():
                raise ValueError(f"--set expects KEY=VALUE, got {item!r}")
            out[key.strip()] = value.strip()
        return out
