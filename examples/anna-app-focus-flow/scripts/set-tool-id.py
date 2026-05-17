#!/usr/bin/env python3
"""Apply / reset minted Executa IDs across this example.

The Focus Flow example needs the *same* Tool ID written into four files:

    - executas/focus-session-python/pyproject.toml      ([project].name + [project.scripts])
    - executas/focus-session-python/focus_session_plugin.py   (MANIFEST["name"])
    - manifest.json                              (required_executas + ui.host_api.tools)
    - bundle/app.js                              (TOOL_ID constant)

…and the Skill ID into one:

    - manifest.json                              (required_executas)

Forgetting any of them produces silent runtime failures (Stopped card,
``tools.invoke`` timeout, etc.).  This script does the substitution
atomically and supports a clean reset back to the ``*-CHANGEME-*``
placeholders so the repo stays publishable.

Usage::

    # After minting on https://anna.partners/executa:
    scripts/set-tool-id.py apply \\
        --tool  tool-yourhandle-focus-session-abcd1234 \\
        --skill skill-yourhandle-focus-coach-efgh5678

    # Inspect what is currently wired in:
    scripts/set-tool-id.py status

    # Reset every file back to placeholders before committing:
    scripts/set-tool-id.py reset

The script is idempotent and refuses to overwrite a previously-minted ID
without ``--force``. Substitutions are anchored to specific syntactic
positions per file, so example IDs inside comments/docstrings are NOT
touched.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOOL_PLACEHOLDER = "tool-CHANGEME-focus-session-CHANGEME"
SKILL_PLACEHOLDER = "skill-CHANGEME-focus-coach-CHANGEME"

# Anna mints IDs of the form ``{kind}-{handle}-{slug}-{uniq}`` where
# handle/uniq are alphanumeric (handle may contain inner hyphens).
MINTED_TOOL_RE = re.compile(r"^tool-[A-Za-z0-9-]+-focus-session-[A-Za-z0-9]+$")
MINTED_SKILL_RE = re.compile(r"^skill-[A-Za-z0-9-]+-focus-coach-[A-Za-z0-9]+$")

# Inner ID shapes (no anchors) for use inside per-anchor patterns.
ID_PART_TOOL = (
    r"tool-(?:CHANGEME-focus-session-CHANGEME"
    r"|[A-Za-z0-9-]+-focus-session-[A-Za-z0-9]+)"
)
ID_PART_SKILL = (
    r"skill-(?:CHANGEME-focus-coach-CHANGEME"
    r"|[A-Za-z0-9-]+-focus-coach-[A-Za-z0-9]+)"
)


@dataclass(frozen=True)
class Anchor:
    """Describes one syntactic position holding an ID inside a file."""

    path: Path
    # Regex with a single capture group around the ID.
    pattern: re.Pattern[str]
    description: str


# ----- Tool ID anchors ----------------------------------------------------
TOOL_ANCHORS: list[Anchor] = [
    # pyproject.toml: name = "<id>"
    Anchor(
        path=ROOT / "executas" / "focus-session" / "pyproject.toml",
        pattern=re.compile(rf'(?m)^name\s*=\s*"({ID_PART_TOOL})"\s*$'),
        description="pyproject.toml [project].name",
    ),
    # pyproject.toml: [project.scripts] "<id>" = "focus_session_plugin:main"
    Anchor(
        path=ROOT / "executas" / "focus-session" / "pyproject.toml",
        pattern=re.compile(
            rf'(?m)^"({ID_PART_TOOL})"\s*=\s*"focus_session_plugin:main"\s*$'
        ),
        description="pyproject.toml [project.scripts] entry",
    ),
    # focus_session_plugin.py: MANIFEST {"name": "<id>", ...}
    Anchor(
        path=ROOT / "executas" / "focus-session" / "focus_session_plugin.py",
        pattern=re.compile(rf'(?m)^\s*"name":\s*"({ID_PART_TOOL})",\s*$'),
        description="focus_session_plugin.py MANIFEST['name']",
    ),
    # manifest.json: every "tool_id": "tool-..."
    Anchor(
        path=ROOT / "manifest.json",
        pattern=re.compile(rf'"tool_id":\s*"({ID_PART_TOOL})"'),
        description="manifest.json required_executas[].tool_id",
    ),
    # manifest.json: ui.host_api.tools entries (with required:/optional: prefix or bare)
    Anchor(
        path=ROOT / "manifest.json",
        pattern=re.compile(rf'"(?:required:|optional:)?({ID_PART_TOOL})"'),
        description="manifest.json ui.host_api.tools",
    ),
    # bundle/app.js: const TOOL_ID = "<id>";
    Anchor(
        path=ROOT / "bundle" / "app.js",
        pattern=re.compile(rf'(?m)^const\s+TOOL_ID\s*=\s*"({ID_PART_TOOL})"\s*;\s*$'),
        description="bundle/app.js TOOL_ID",
    ),
]

# ----- Skill ID anchors ---------------------------------------------------
SKILL_ANCHORS: list[Anchor] = [
    Anchor(
        path=ROOT / "manifest.json",
        pattern=re.compile(rf'"tool_id":\s*"({ID_PART_SKILL})"'),
        description="manifest.json required_executas[].tool_id (skill)",
    ),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _collect_ids(anchors: list[Anchor]) -> set[str]:
    ids: set[str] = set()
    for a in anchors:
        for m in a.pattern.finditer(_read(a.path)):
            ids.add(m.group(1))
    return ids


def _current_id(anchors: list[Anchor], placeholder: str) -> str | None:
    ids = _collect_ids(anchors)
    ids.discard(placeholder)
    if not ids:
        return None
    if len(ids) > 1:
        raise SystemExit(
            f"❌ Inconsistent IDs across anchors: {sorted(ids)}. "
            f"Run ``reset`` then ``apply`` again."
        )
    return next(iter(ids))


def _replace_in_match(match: re.Match[str], new_id: str) -> str:
    """Rebuild the matched text with capture group 1 swapped for ``new_id``."""
    full = match.group(0)
    rel_start = match.start(1) - match.start(0)
    rel_end = match.end(1) - match.start(0)
    return full[:rel_start] + new_id + full[rel_end:]


def _apply(anchors: list[Anchor], placeholder: str, new_id: str, force: bool) -> None:
    current = _current_id(anchors, placeholder)
    if current and current != new_id and not force:
        raise SystemExit(
            f"❌ Files already wired to {current!r}; refusing to overwrite "
            f"with {new_id!r} without --force. Run ``reset`` first."
        )
    by_path: dict[Path, list[Anchor]] = {}
    for a in anchors:
        by_path.setdefault(a.path, []).append(a)
    touched = False
    for path, group in by_path.items():
        text = _read(path)
        new_text = text
        for a in group:
            new_text = a.pattern.sub(lambda m: _replace_in_match(m, new_id), new_text)
        if new_text != text:
            _write(path, new_text)
            touched = True
            print(f"  ✔ {path.relative_to(ROOT)}")
    if not touched:
        print("  (no changes)")


def cmd_status(_args: argparse.Namespace) -> int:
    tool = _current_id(TOOL_ANCHORS, TOOL_PLACEHOLDER)
    skill = _current_id(SKILL_ANCHORS, SKILL_PLACEHOLDER)
    print(f"tool_id  : {tool or '(placeholder)'}")
    print(f"skill_id : {skill or '(placeholder)'}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.tool and not args.skill:
        raise SystemExit("❌ Provide at least one of --tool / --skill.")
    if args.tool:
        if not MINTED_TOOL_RE.match(args.tool):
            raise SystemExit(
                f"❌ --tool must look like ``tool-<handle>-focus-session-<uniq>``,"
                f" got {args.tool!r}"
            )
        print(f"Wiring tool_id  → {args.tool}")
        _apply(TOOL_ANCHORS, TOOL_PLACEHOLDER, args.tool, args.force)
    if args.skill:
        if not MINTED_SKILL_RE.match(args.skill):
            raise SystemExit(
                f"❌ --skill must look like ``skill-<handle>-focus-coach-<uniq>``,"
                f" got {args.skill!r}"
            )
        print(f"Wiring skill_id → {args.skill}")
        _apply(SKILL_ANCHORS, SKILL_PLACEHOLDER, args.skill, args.force)
    return 0


def cmd_reset(_args: argparse.Namespace) -> int:
    print("Resetting tool_id  → placeholder")
    _apply(TOOL_ANCHORS, TOOL_PLACEHOLDER, TOOL_PLACEHOLDER, force=True)
    print("Resetting skill_id → placeholder")
    _apply(SKILL_ANCHORS, SKILL_PLACEHOLDER, SKILL_PLACEHOLDER, force=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print currently wired IDs.")
    p_status.set_defaults(func=cmd_status)

    p_apply = sub.add_parser("apply", help="Wire minted IDs into all files.")
    p_apply.add_argument(
        "--tool", help="Minted Tool ID (tool-<handle>-focus-session-<uniq>)."
    )
    p_apply.add_argument(
        "--skill", help="Minted Skill ID (skill-<handle>-focus-coach-<uniq>)."
    )
    p_apply.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing minted ID without erroring.",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_reset = sub.add_parser(
        "reset", help="Reset all files back to CHANGEME placeholders."
    )
    p_reset.set_defaults(func=cmd_reset)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
