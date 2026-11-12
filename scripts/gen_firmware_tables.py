"""Generate src/fpv_tools/firmware/tables/betaflight_*.py from the Betaflight firmware source.

    uv run scripts/gen_firmware_tables.py path/to/betaflight            # all release lines, write
    uv run scripts/gen_firmware_tables.py path/to/betaflight --check    # the files still match their recorded commits
    uv run scripts/gen_firmware_tables.py path/to/betaflight --check --heads  # what newer firmware would change
    uv run scripts/gen_firmware_tables.py path/to/betaflight 2026.6=e92c10887

What this produces, and what it assumes (see also the firmware package docstring):

- Each release line is read from the newest commit that belongs to it: its maintenance branch head (the largest
  enum any firmware of that line can have). A different ref can be given per line.
- It extracts *names by value* of a few enums and the values of a few #defines, parsed with tree-sitter's C grammar.
  What those values mean in logs and over MSP rests on firmware statements listed in scripts/firmware_anchors.py;
  every one must be present exactly once (comments ignored) or the line is not generated.
- It refuses anything it cannot read exactly: a missing enum or #define, preprocessor conditionals inside an enum
  (indices would depend on the build target), values it cannot evaluate, gaps, or a missing count sentinel.
- Committed tables only grow: a table may gain entries at the end, nothing else. A rename at the same index is
  allowed with --allow-rename and recorded as an alias (old logs used the old name); anything else needs
  --allow-rewrite, because it would silently re-map every log recorded with that release line.
"""

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_c
from firmware_anchors import ANCHORS, RAW
from tree_sitter import Language, Node, Parser

PARSER = Parser(Language(tree_sitter_c.language()))
OUT = Path(__file__).resolve().parents[1] / "src" / "fpv_tools" / "firmware" / "tables"
LINES = {
    "4.5": "origin/4.5-maintenance",
    "2025.12": "origin/2025.12-maintenance",
    "2026.6": "origin/2026.6-maintenance",
}


@dataclass(frozen=True)
class EnumSpec:
    path: str
    typedef: str
    prefix: str  # stripped from names
    sentinel: str | None  # trailing count entry that is not a value
    # Conditional blocks inside the enum that are known never to be part of flight firmware, skipped explicitly;
    # any other conditional makes generation fail
    excluded_conditions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DefineSpec:
    path: str
    name: str


ENUMS = {
    "BOX_IDS": EnumSpec("src/main/fc/rc_modes.h", "boxId_e", "BOX", "CHECKBOX_ITEM_COUNT"),
    "DEBUG_MODES": EnumSpec("src/main/build/debug.h", "debugType_e", "DEBUG_", "DEBUG_COUNT"),
    "FAILSAFE_PHASES": EnumSpec("src/main/flight/failsafe.h", "failsafePhase_e", "FAILSAFE_", None),
    "SERIALRX_PROVIDERS": EnumSpec("src/main/rx/rx.h", "SerialRXType", "SERIALRX_", None),
    # Logged in DISARM events; DISARM_REASON_SYSTEM = 255 exists only in the firmware's unit-test builds
    "DISARM_REASONS": EnumSpec(
        "src/main/fc/core.h", "flightLogDisarmReason_e", "DISARM_REASON_", None, frozenset({"UNIT_TEST"})
    ),
    # Logged in INFLIGHT_ADJUSTMENT events
    "ADJUSTMENT_FUNCTIONS": EnumSpec(
        "src/main/fc/rc_adjustments.h", "adjustmentFunction_e", "ADJUSTMENT_", "ADJUSTMENT_FUNCTION_COUNT"
    ),
}
FEATURES = EnumSpec("src/main/config/feature.h", "features_e", "FEATURE_", None)  # bit masks, not indices
DEFINES = {
    "API_VERSION_MINOR": DefineSpec("src/main/msp/msp_protocol.h", "API_VERSION_MINOR"),
    "CRSF_RSSI_MIN": DefineSpec("src/main/rx/crsf.h", "CRSF_RSSI_MIN"),
    "CRSF_RSSI_MAX": DefineSpec("src/main/rx/crsf.h", "CRSF_RSSI_MAX"),
    "RSSI_MAX_VALUE": DefineSpec("src/main/rx/rx.h", "RSSI_MAX_VALUE"),
    "SLOW_VOLTAGE_TASK_FREQ_HZ": DefineSpec("src/main/sensors/voltage.h", "SLOW_VOLTAGE_TASK_FREQ_HZ"),
    "FAST_VOLTAGE_TASK_FREQ_HZ": DefineSpec("src/main/sensors/voltage.h", "FAST_VOLTAGE_TASK_FREQ_HZ"),
}


class GenerationError(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def source(repo: Path, ref: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, check=True).stdout


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from walk(child)


def text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


def evaluate(node: Node, names: dict[str, int]) -> int:
    """A constant C expression: numbers, earlier names, parentheses, unary minus, + - * << |."""
    match node.type:
        case "number_literal":
            return int(text(node).rstrip("uUlL"), 0)
        case "identifier" if text(node) in names:
            return names[text(node)]
        case "parenthesized_expression":
            return evaluate(node.named_children[0], names)
        case "unary_expression" if text(node).startswith("-"):
            return -evaluate(node.named_children[0], names)
        case "binary_expression":
            left, right = (evaluate(c, names) for c in node.named_children)
            operator = text(node.child_by_field_name("operator"))
            ops = {"+": left + right, "-": left - right, "*": left * right, "<<": left << right, "|": left | right}
            if operator in ops:
                return ops[operator]
        case _:
            pass
    raise GenerationError(f"cannot evaluate {node.type} {text(node)!r}")


def enumerators(repo: Path, ref: str, spec: EnumSpec) -> list[tuple[str, int, bool]]:
    """(name, value, is_alias) of a typedef'd enum, in source order."""
    tree = PARSER.parse(source(repo, ref, spec.path))
    body = None
    for node in walk(tree.root_node):
        if node.type == "type_definition" and text(node.child_by_field_name("declarator")) == spec.typedef:
            body = next((c for c in walk(node) if c.type == "enumerator_list"), None)
            break
    if body is None:
        raise GenerationError(f"{ref}: enum {spec.typedef} not found in {spec.path}")
    items: list[Node] = []
    for child in body.children:
        if child.type == "enumerator":
            items.append(child)
        elif child.type.startswith("preproc"):
            condition = text(child.child_by_field_name("name") or child.child_by_field_name("condition"))
            if child.type != "preproc_ifdef" or condition not in spec.excluded_conditions:
                raise GenerationError(
                    f"{ref}: enum {spec.typedef} has a preprocessor conditional ({condition or child.type}); "
                    "indices would depend on the build"
                )
    out: list[tuple[str, int, bool]] = []
    names: dict[str, int] = {}
    following = 0
    for item in items:
        name, value_node = text(item.child_by_field_name("name")), item.child_by_field_name("value")
        value = evaluate(value_node, names) if value_node is not None else following
        names[name], following = value, value + 1
        out.append((name, value, value_node is not None and value_node.type == "identifier"))
    return out


def index_table(repo: Path, ref: str, spec: EnumSpec) -> tuple[list[str], int | None]:
    """Names by value 0..n-1 (aliases and the count sentinel left out), and BOXID_FLIGHTMODE_LAST if present."""
    entries = enumerators(repo, ref, spec)
    if spec.sentinel is not None:
        if not entries or entries[-1][0] != spec.sentinel:
            raise GenerationError(f"{ref}: {spec.typedef} does not end with {spec.sentinel}")
        entries = entries[:-1]
    real = [(name, value) for name, value, alias in entries if not alias]
    if [value for _, value in real] != list(range(len(real))):
        raise GenerationError(f"{ref}: {spec.typedef} values are not 0..n-1")
    flight_mode_last = next((v for n, v, _ in entries if n == "BOXID_FLIGHTMODE_LAST"), None)
    return [name.removeprefix(spec.prefix) for name, _ in real], flight_mode_last


def define(repo: Path, ref: str, spec: DefineSpec) -> int:
    tree = PARSER.parse(source(repo, ref, spec.path))
    for node in walk(tree.root_node):
        if node.type == "preproc_def" and text(node.child_by_field_name("name")) == spec.name:
            parsed = PARSER.parse(f"int x = {text(node.child_by_field_name('value'))};".encode())
            init = next(c for c in walk(parsed.root_node) if c.type == "init_declarator")
            value = init.child_by_field_name("value")
            if value is None:
                break
            return evaluate(value, {})
    raise GenerationError(f"{ref}: #define {spec.name} not found in {spec.path}")


def without_comments(code: bytes) -> str:
    """Source with comments removed (by parse tree, so comment markers inside strings stay) and whitespace
    normalised to single spaces."""
    tree = PARSER.parse(code)
    parts: list[bytes] = []
    position = 0
    for node in walk(tree.root_node):
        if node.type == "comment":
            parts.append(code[position : node.start_byte])
            position = node.end_byte
    parts.append(code[position:])
    return re.sub(r"\s+", " ", b" ".join(parts).decode(errors="replace"))


def check_anchors(repo: Path, ref: str) -> None:
    """Every anchor must occur exactly once (see scripts/firmware_anchors.py)."""
    sources: dict[str, str] = {}
    problems: list[str] = []
    for user, anchors in ANCHORS.items():
        for path, code in anchors:
            alternatives = (code,) if isinstance(code, str) else code
            raw = path.startswith(RAW)
            path = path.removeprefix(RAW)
            key = ("raw:" if raw else "") + path
            if key not in sources:
                code_bytes = source(repo, ref, path)
                sources[key] = (
                    re.sub(r"\s+", " ", code_bytes.decode(errors="replace")) if raw else without_comments(code_bytes)
                )
            counts = [sources[key].count(re.sub(r"\s+", " ", alt).strip()) for alt in alternatives]
            if sum(counts) != 1:
                state = "not found" if not sum(counts) else "found more than once"
                problems.append(f"  {path}: {state}: {alternatives[0]}\n    relied on by {user}")
    if problems:
        listing = "\n".join(problems)
        raise GenerationError(f"{ref}: firmware statements changed; review the fpv-tools code named:\n{listing}")


def committed(path: Path) -> dict[str, object]:
    """Module-level literal assignments of an existing generated file."""
    if not path.exists():
        return {}
    out: dict[str, object] = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
            if isinstance(target, ast.Name) and node.value is not None:
                out[target.id] = ast.literal_eval(node.value)
    return out


def grow(
    name: str, old: tuple[str, ...], new: list[str], aliases: dict[str, list[str]], args: argparse.Namespace
) -> None:
    """Enforce only-grow against the committed table; record renames as aliases (current name -> old names)."""
    if len(new) < len(old) and not args.allow_rewrite:
        raise GenerationError(f"{name}: {len(old) - len(new)} entries removed; --allow-rewrite to accept")
    for index, (before, after) in enumerate(zip(old, new, strict=False)):
        if before == after:
            continue
        if before in new and not args.allow_rewrite:
            raise GenerationError(f"{name}: {before} moved from index {index}; --allow-rewrite to accept")
        if not (args.allow_rename or args.allow_rewrite):
            raise GenerationError(f"{name}[{index}]: {before} renamed to {after}; --allow-rename to record an alias")
        aliases.setdefault(after, []).append(before)


def generate(repo: Path, line: str, ref: str, path: Path, args: argparse.Namespace, label: str | None = None) -> str:
    """The table file for a line read at `ref`; `label` names the ref in its docstring (default: `ref`)."""
    check_anchors(repo, ref)
    previous = committed(path)
    old_aliases = previous.get("ALIASES", {})
    aliases: dict[str, list[str]] = {}
    if isinstance(old_aliases, dict):
        aliases = {str(k): list(v) for k, v in old_aliases.items()}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    commit = git(repo, "log", "-1", "--format=%h %cs", ref).strip()
    out = [
        f'"""Betaflight {line} line: enums and constants from {label or ref} ({commit}).',
        "",
        'Generated by scripts/gen_firmware_tables.py; do not edit. Names are by value (index)."""',
        "",
    ]
    flight_mode_last = None
    for name, spec in ENUMS.items():
        names, last = index_table(repo, ref, spec)
        flight_mode_last = last if last is not None else flight_mode_last
        old = previous.get(name)
        if isinstance(old, tuple):
            entries: tuple[object, ...] = old  # pyright: ignore[reportUnknownVariableType]
            grow(name, tuple(str(x) for x in entries), names, aliases, args)
        body = "".join(f'    "{n}",\n' for n in names)
        out.append(f"{name}: tuple[str, ...] = (  # {spec.path} {spec.typedef}\n{body})")
    features = {n.removeprefix(FEATURES.prefix): v for n, v, alias in enumerators(repo, ref, FEATURES) if not alias}
    body = "".join(f'    "{n}": {v},\n' for n, v in features.items())
    out.append(f"FEATURES: dict[str, int] = {{  # {FEATURES.path} {FEATURES.typedef}, bit masks\n{body}}}")
    if flight_mode_last is None:
        raise GenerationError(f"{ref}: BOXID_FLIGHTMODE_LAST not found")
    out.append(f"BOXID_FLIGHTMODE_LAST = {flight_mode_last}  # src/main/fc/rc_modes.h boxId_e")
    for name, spec in DEFINES.items():
        out.append(f"{name} = {define(repo, ref, spec)}  # {spec.path}")
    body = "".join(f'    "{k}": {tuple(v)!r},\n' for k, v in sorted(aliases.items()))
    out.append(
        f"ALIASES: dict[str, tuple[str, ...]] = {{  # current name -> names older builds of the line used\n{body}}}"
    )
    return "\n".join(out) + "\n"


def recorded(path: Path) -> tuple[str, str]:
    """(ref name, commit) a committed table file was generated from, from its docstring."""
    m = re.match(r'"""Betaflight \S+ line: enums and constants from (\S+) \((\w+) ', path.read_text())
    if not m:
        raise GenerationError(f"{path.name}: no recorded source commit")
    return m.group(1), m.group(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path, help="Betaflight firmware checkout")
    parser.add_argument("refs", nargs="*", help="LINE=REF to read a line from another ref (default: maintenance heads)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare with the committed files, reading each line at the commit it records; write nothing",
    )
    parser.add_argument("--heads", action="store_true", help="with --check: read the maintenance heads instead")
    parser.add_argument("--allow-rename", action="store_true", help="accept renames at the same index (as aliases)")
    parser.add_argument("--allow-rewrite", action="store_true", help="accept removals and reorders")
    args = parser.parse_args()
    given = dict(r.split("=", 1) for r in args.refs)
    OUT.mkdir(exist_ok=True)
    drift = False
    try:
        for line, head in LINES.items():
            path = OUT / f"betaflight_{line.replace('.', '_')}.py"
            ref, label = given.get(line, head), None
            if args.check and not args.heads and line not in given:
                label, ref = recorded(path)  # reproducible: what the file was generated from
            content = generate(args.repo.expanduser(), line, ref, path, args, label)
            if path.exists() and path.read_text() == content:
                continue
            drift = True
            print(f"{path.name}: {'differs from' if args.check else 'written from'} {ref}", file=sys.stderr)
            if not args.check:
                path.write_text(content)
    except GenerationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 1 if args.check and drift else 0


if __name__ == "__main__":
    sys.exit(main())
