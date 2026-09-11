#!/usr/bin/env python3
"""Lightweight layout and frontmatter check the installers bundle themselves.

Installation must not depend on the system skill-creator being present, so the
installers run this instead and treat the system validator as an optional extra.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ALLOWED_FRONTMATTER = {"name", "description", "license", "metadata", "allowed-tools"}
REQUIRED_FRONTMATTER = {"name", "description"}


def check_layout(skill_dir: str | Path) -> list[dict[str, str]]:
    root = Path(skill_dir)
    problems: list[dict[str, str]] = []
    if not root.is_dir():
        return [{"code": "SKILL_DIR_MISSING", "message": "Skill directory does not exist", "path": str(root)}]
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        return [{"code": "SKILL_MD_MISSING", "message": "SKILL.md is required", "path": "SKILL.md"}]
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        problems.append({"code": "FRONTMATTER_MISSING", "message": "SKILL.md must start with YAML frontmatter", "path": "SKILL.md"})
        return problems
    end = text.find("\n---\n", 4)
    if end < 0:
        problems.append({"code": "FRONTMATTER_UNTERMINATED", "message": "Frontmatter block is not closed", "path": "SKILL.md"})
        return problems
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.startswith(" ") or line.startswith("#"):
            continue
        if ":" not in line:
            problems.append({"code": "FRONTMATTER_LINE_INVALID", "message": f"Invalid frontmatter line: {line!r}", "path": "SKILL.md"})
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    for key in sorted(set(fields) - ALLOWED_FRONTMATTER):
        problems.append({"code": "FRONTMATTER_KEY_UNKNOWN", "message": f"Unexpected frontmatter key {key!r}", "path": "SKILL.md"})
    for key in sorted(REQUIRED_FRONTMATTER - set(fields)):
        problems.append({"code": "FRONTMATTER_KEY_MISSING", "message": f"Frontmatter needs {key!r}", "path": "SKILL.md"})
    if fields.get("name") and fields["name"] != root.name:
        problems.append({"code": "SKILL_NAME_MISMATCH", "message": f"Frontmatter name {fields['name']!r} must equal the directory name {root.name!r}", "path": "SKILL.md"})
    description = fields.get("description", "")
    if description and not description.startswith("Use when"):
        problems.append({"code": "SKILL_DESCRIPTION_INVALID", "message": "The description must start with 'Use when'", "path": "SKILL.md"})
    if description and "Do not use" not in description:
        problems.append({"code": "SKILL_DESCRIPTION_INVALID", "message": "The description must state what the skill is not for", "path": "SKILL.md"})
    if not (root / "agents" / "openai.yaml").is_file():
        problems.append({"code": "AGENTS_METADATA_MISSING", "message": "agents/openai.yaml is required", "path": "agents/openai.yaml"})
    if not (root / "scripts").is_dir():
        problems.append({"code": "SCRIPTS_MISSING", "message": "A scripts directory is required", "path": "scripts"})
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        problems = check_layout(args.skill_dir)
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    if problems:
        for row in problems:
            print(f"{row['code']}: {row['message']}: {row['path']}", file=sys.stderr)
        return 3
    print("layout ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["check_layout"]
