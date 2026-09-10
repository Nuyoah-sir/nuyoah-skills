#!/usr/bin/env python3
"""Create a non-overwriting evidence-delta workspace from a sealed base and valid requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

from validate_bundle import validate_bundle
from validate_delta import delta_digest
from validate_evidence_requests import _ids_from_bundle, validate_requests


def initialize_delta(base_dir: str | Path, requests_path: str | Path, delta_dir: str | Path) -> Path:
    base = Path(base_dir).resolve()
    requests_file = Path(requests_path).resolve()
    delta = Path(delta_dir).resolve()
    base_report = validate_bundle(base, require_seal=True)
    if base_report["result"] != "pass":
        raise ValueError("Base bundle failed sealed validation: " + json.dumps(base_report, ensure_ascii=False))
    requests = json.loads(requests_file.read_text(encoding="utf-8"))
    models, rubrics, package_id = _ids_from_bundle(base)
    request_report = validate_requests(requests, models, rubrics, package_id)
    if request_report["result"] != "pass":
        raise ValueError("Evidence requests are invalid: " + json.dumps(request_report, ensure_ascii=False))
    if delta.exists():
        raise FileExistsError(f"Delta directory already exists: {delta}")

    manifest = json.loads((base / "MANIFEST.json").read_text(encoding="utf-8"))
    request_ids = [item["request_id"] for item in requests["requests"]]
    delta.mkdir(parents=True)
    shutil.copyfile(requests_file, delta / "request-copy.json")
    metadata = {
        "schema": "vibe-evals-evidence-delta",
        "schema_version": "1.0.0",
        "delta_id": "delta-" + uuid.uuid4().hex,
        "base_package_id": package_id,
        "base_digest": delta_digest(base),
        "request_sha256": hashlib.sha256(requests_file.read_bytes()).hexdigest(),
        "source_input_digest": manifest["source_input_digest"],
        "request_ids": request_ids,
        "response_count": len(request_ids),
    }
    (delta / "DELTA.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (delta / "responses.jsonl").write_text("", encoding="utf-8")
    (delta / "run-state.json").write_text(json.dumps({"schema_version": "1.0.0", "phase": "INITIALIZED", "requests": {request_id: {"status": "PENDING", "attempts": 0} for request_id in request_ids}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return delta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_dir", type=Path)
    parser.add_argument("requests", type=Path)
    parser.add_argument("delta_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        print(initialize_delta(args.base_dir, args.requests, args.delta_dir))
    except (ValueError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
