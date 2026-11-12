"""Release a new version: the tag this pushes makes .github/workflows/release.yml publish it on PyPI and GitHub.

uv run scripts/release.py patch|minor|major

Run on main, in step with origin, with a clean tree and the release notes under [Unreleased] in CHANGELOG.md. It runs
the checks CI runs, bumps the version, moves the notes under the new version, commits, tags vX.Y.Z and, if you
confirm, pushes the commit and the tag.
"""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
BUMPS = ("patch", "minor", "major")
CHECKS = (("ruff", "check"), ("ruff", "format", "--check"), ("pyright",), ("pytest",))
UNRELEASED_NOTES = re.compile(r"^## \[Unreleased\]\n(.*?)(?=^## \[|^\[)", re.M | re.S)
UNRELEASED_LINK = re.compile(r"^\[Unreleased\]: (\S+)/compare/v(\S+)\.\.\.HEAD$", re.M)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _uv(*args: str) -> str:
    return subprocess.run(["uv", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def changelog(text: str, old: str, new: str, day: str) -> str:
    """The changelog with the [Unreleased] notes under a heading for the new version, and its comparison link."""
    notes = UNRELEASED_NOTES.search(text)
    if notes is None or not notes[1].strip():
        raise SystemExit("CHANGELOG.md: no notes under [Unreleased]")
    link = UNRELEASED_LINK.search(text)
    if link is None or link[2] != old:
        raise SystemExit(f"CHANGELOG.md: no link [Unreleased]: .../compare/v{old}...HEAD")
    repo = link[1]
    links = f"[Unreleased]: {repo}/compare/v{new}...HEAD\n[{new}]: {repo}/compare/v{old}...v{new}"
    text = text[: link.start()] + links + text[link.end() :]
    return text[: notes.start(1)] + f"\n## [{new}] - {day}\n" + text[notes.start(1) :]


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in BUMPS:
        raise SystemExit(__doc__)
    bump = sys.argv[1]

    if _git("branch", "--show-current") != "main":
        raise SystemExit("not on main")
    if _git("status", "--porcelain"):
        raise SystemExit("uncommitted changes")
    _git("fetch", "origin", "main")
    if _git("rev-parse", "HEAD") != _git("rev-parse", "origin/main"):
        raise SystemExit("main is not the same as origin/main")
    old = _uv("version", "--short")
    new = _uv("version", "--bump", bump, "--dry-run", "--short")
    if _git("tag", "--list", f"v{new}"):
        raise SystemExit(f"v{new} is already tagged")
    text = changelog(CHANGELOG.read_text(), old, new, date.today().isoformat())

    for check in CHECKS:
        print(f"$ uv run {' '.join(check)}", file=sys.stderr)
        if subprocess.run(["uv", "run", *check], cwd=ROOT).returncode:
            raise SystemExit(f"{check[0]} failed: nothing changed")

    _uv("version", "--bump", bump)
    CHANGELOG.write_text(text)
    _git("commit", "--all", "--message", f"Release {new}")
    _git("tag", "--annotate", f"v{new}", "--message", f"fpv-tools {new}")
    print(f"Committed and tagged {old} => {new}", file=sys.stderr)

    push = ("git", "push", "--atomic", "origin", "main", f"v{new}")
    try:
        answer = input(f"Push v{new}? This publishes {new} on PyPI, which cannot be undone [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        subprocess.run(push, cwd=ROOT, check=True)
    else:
        print(f"Not pushed. To push: {' '.join(push)}", file=sys.stderr)
        print(f"To undo: git tag --delete v{new} && git reset --hard HEAD~1", file=sys.stderr)


if __name__ == "__main__":
    main()
