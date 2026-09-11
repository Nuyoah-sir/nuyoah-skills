#!/usr/bin/env python3
"""Extract exact contiguous UTF-8 source lines with source and excerpt hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath


def extract_excerpt(source_root: str | Path, relative_path: str, line_start: int, line_end: int, output_path: str | Path, blob_dir: str | Path | None = None, bundle_root: str | Path | None = None) -> dict:
    root = Path(source_root).resolve()
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    posix = PurePosixPath(relative_path.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or not posix.parts or ":" in posix.parts[0]:
        raise ValueError("Source path must be a safe relative path")
    source = (root / posix).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("Source path escapes source_root") from exc
    if source.is_symlink() or not source.is_file():
        raise ValueError("Source must be a regular non-symlink file")
    if not isinstance(line_start, int) or isinstance(line_start, bool) or not isinstance(line_end, int) or isinstance(line_end, bool) or line_start < 1 or line_end < line_start:
        raise ValueError("Invalid line range")
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines()
    if line_end > len(lines):
        raise ValueError(f"Line range exceeds file length {len(lines)}")
    excerpt = "\n".join(lines[line_start - 1:line_end])
    file_digest = hashlib.sha256(raw).hexdigest()
    result = {"path": posix.as_posix(), "line_start": line_start, "line_end": line_end, "file_sha256": file_digest, "excerpt": excerpt, "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest()}
    if (blob_dir is None) != (bundle_root is None):
        raise ValueError("blob_dir and bundle_root must be provided together")
    if blob_dir is not None:
        blobs = Path(blob_dir).resolve()
        bundle = Path(bundle_root).resolve()
        try:
            blobs.relative_to(bundle)
        except ValueError as exc:
            raise ValueError("blob_dir must be inside bundle_root") from exc
        suffix = source.suffix.lower() if source.suffix else ".bin"
        blob = blobs / f"{file_digest}{suffix}"
        if blob.exists() and blob.read_bytes() != raw:
            raise ValueError("Existing content-addressed blob does not match source")
        blob.parent.mkdir(parents=True, exist_ok=True)
        if not blob.exists():
            blob.write_bytes(raw)
        result["source_blob_path"] = blob.relative_to(bundle).as_posix()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("relative_path")
    parser.add_argument("line_start", type=int)
    parser.add_argument("line_end", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument("--blob-dir", type=Path)
    parser.add_argument("--bundle-root", type=Path)
    args = parser.parse_args(argv)
    try:
        extract_excerpt(args.source_root, args.relative_path, args.line_start, args.line_end, args.output, args.blob_dir, args.bundle_root)
    except (ValueError, FileExistsError, OSError, UnicodeDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
