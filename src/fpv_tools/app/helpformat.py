"""Help as plain lines, coloured: no boxes, and no lines padded to the window width (both break when the terminal is
resized). Paragraphs are wrapped; indented blocks (examples, tables) are kept as written."""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING, Any

from rich.text import Text

if TYPE_CHECKING:
    from cyclopts.help import HelpEntry, HelpPanel
    from rich.console import Console, ConsoleOptions

MAX_WIDTH = 100
LABEL_WIDTH = 24  # labels up to this long share the line with their description


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "primary_renderable"):  # cyclopts' InlineText: a description with texts appended to it
        return " ".join(filter(None, [_plain(value.primary_renderable), *map(_plain, value.texts)]))
    return str(value.plain if hasattr(value, "plain") else value).strip()


def _styled(line: str) -> Text:
    """`code` in colour instead of quoted."""
    text = Text()
    for i, part in enumerate(line.split("`")):
        text.append(part, style="cyan" if i % 2 else "")
    return text


def _width(console: Console) -> int:
    return min(console.width, MAX_WIDTH) - 1


def _paragraphs(text: str, width: int, indent: str = "") -> list[str]:
    """Wrapped prose paragraphs; blocks whose lines are indented stay as they are."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        lines = block.splitlines()
        if all(line.startswith((" ", "\t")) for line in lines):
            out += [indent + line.rstrip() for line in lines]
        else:
            out += textwrap.wrap(
                " ".join(line.strip() for line in lines), width, initial_indent=indent, subsequent_indent=indent
            ) or [""]
        out.append("")
    return out[:-1]


class HelpFormatter:
    def render_usage(self, console: Console, options: ConsoleOptions, usage: Any) -> None:
        if text := _plain(usage):
            text = text if text.startswith("Usage:") else f"Usage: {text}"
            console.print(Text(text, style="bold"), soft_wrap=True)
            console.print()

    def render_description(self, console: Console, options: ConsoleOptions, description: Any) -> None:
        if text := _plain(description):
            for line in _paragraphs(text, _width(console)):
                console.print(_styled(line), soft_wrap=True)
            console.print()

    def __call__(self, console: Console, options: ConsoleOptions, panel: HelpPanel) -> None:
        if not panel.entries:
            return
        width = _width(console)
        console.print(Text(f"{_plain(panel.title)}:", style="bold"), soft_wrap=True)
        if description := _plain(panel.description):
            for line in _paragraphs(description, width, "  "):
                console.print(_styled(line), soft_wrap=True)
        labels = [self._label(entry, panel.format) for entry in panel.entries]
        column = min(max(len(label) for label in labels), LABEL_WIDTH) + 4
        for entry, label in zip(panel.entries, labels, strict=True):
            text = self._description(entry)
            lines = textwrap.wrap(text, max(width - column, 30)) or [""]
            first = Text("  ") + Text(label, style="cyan")
            if len(label) + 4 <= column:
                first.append(" " * (column - 2 - len(label))).append_text(_styled(lines.pop(0)))
            console.print(first, soft_wrap=True)
            for line in lines:
                console.print(Text(" " * column) + _styled(line), soft_wrap=True)
        console.print()

    @staticmethod
    def _label(entry: HelpEntry, kind: str) -> str:
        if kind == "command" or entry.positional_label:
            return ", ".join(entry.display_labels)
        names = [*entry.positive_shorts, *entry.positive_names, *entry.negative_shorts, *entry.negative_names]
        return ", ".join(names) + (f" {entry.metavar}" if entry.metavar else "")

    @staticmethod
    def _description(entry: HelpEntry) -> str:
        text = " ".join(_plain(entry.description).split())
        if entry.default is not None:
            text += f" [default: {entry.default}]"
        if entry.required:
            text += " [required]"
        return text.strip()
