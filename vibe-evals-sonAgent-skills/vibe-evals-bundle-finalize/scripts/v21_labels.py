#!/usr/bin/env python3
"""Read the checked-in V2.1 label library.

The V2.1 tag vocabulary ships as human-readable Markdown. Agents must never invent
tags, so the legal set is parsed from that Markdown into ``references/v21-labels.json``
and both are kept provably in sync.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

LABEL_SECTIONS = ("Pros", "Cons", "Stylistic Fingerprints")
LABEL_SOURCE = "vibe-evals-bundle-finalize/templates/V2.1标签库.md"
LABELS_JSON = "references/v21-labels.json"


def parse_label_library(markdown: str) -> dict[str, dict[str, list[str]]]:
    """Parse the tag tables of the label library Markdown."""

    groups: dict[str, dict[str, list[str]]] = {section: {} for section in LABEL_SECTIONS}
    section: str | None = None
    subsection: str | None = None
    for line in markdown.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            section = name if name in groups else None
            subsection = None
        elif line.startswith("### ") and section:
            subsection = line[4:].strip().split("·", 1)[-1].strip()
            groups[section].setdefault(subsection, [])
        elif section and subsection and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or not cells[0] or cells[0] == "tag" or set(cells[0]) <= set("-: "):
                continue
            if cells[0] not in groups[section][subsection]:
                groups[section][subsection].append(cells[0])
    return groups


def build_label_document(markdown_path: str | Path) -> dict:
    source = Path(markdown_path)
    raw = source.read_bytes()
    groups = parse_label_library(raw.decode("utf-8"))
    return {
        "schema_version": "2.0.0",
        "source": LABEL_SOURCE,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "labels": groups,
        "counts": {key: sum(len(tags) for tags in value.values()) for key, value in groups.items()},
    }


def load_label_document(shared_root: str | Path) -> dict:
    return json.loads((Path(shared_root) / LABELS_JSON).read_text(encoding="utf-8"))


def allowed_labels(shared_root: str | Path) -> dict[str, set[str]]:
    """Return the legal tag set per section."""

    document = load_label_document(shared_root)
    return {
        section: {tag for tags in document["labels"].get(section, {}).values() for tag in tags}
        for section in LABEL_SECTIONS
    }


def label_groups(shared_root: str | Path, section: str) -> dict[str, list[str]]:
    return dict(load_label_document(shared_root)["labels"].get(section, {}))


__all__ = [
    "LABELS_JSON", "LABEL_SECTIONS", "LABEL_SOURCE", "allowed_labels", "build_label_document",
    "label_groups", "load_label_document", "parse_label_library",
]
