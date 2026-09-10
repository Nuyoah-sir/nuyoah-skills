#!/usr/bin/env python3
"""Validate request-scoped evidence supplementation jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_METHODS = {"static_line", "self_test_run", "probe_run", "conversation", "snapshot_diff", "inventory_fact"}


def validate_requests(value: dict, model_ids: set[str], rubric_ids: set[str], expected_package_id: str | None = None) -> dict:
    errors: list[dict[str, str]] = []
    if value.get("schema_version") != "1.0.0":
        errors.append({"code": "SCHEMA_UNSUPPORTED", "message": "schema_version must be 1.0.0"})
    if expected_package_id is not None and value.get("base_package_id") != expected_package_id:
        errors.append({"code": "BASE_PACKAGE_MISMATCH", "message": "Request targets a different base package"})
    seen: set[str] = set()
    for index, item in enumerate(value.get("requests", [])):
        request_id = item.get("request_id")
        if not request_id or request_id in seen:
            errors.append({"code": "REQUEST_ID_DUPLICATE", "message": f"Missing or duplicate request_id at index {index}"})
        seen.add(request_id)
        if item.get("model_id") not in model_ids:
            errors.append({"code": "MODEL_UNKNOWN", "message": f"Unknown model_id {item.get('model_id')!r}"})
        if item.get("rubric_id") not in rubric_ids:
            errors.append({"code": "RUBRIC_UNKNOWN", "message": f"Unknown rubric_id {item.get('rubric_id')!r}"})
        if not str(item.get("need", "")).strip():
            errors.append({"code": "NEED_EMPTY", "message": f"{request_id!r} does not say what evidence is needed"})
        methods = item.get("allowed_methods", [])
        if not methods or any(method not in ALLOWED_METHODS for method in methods):
            errors.append({"code": "METHOD_INVALID", "message": f"{request_id!r} has empty or invalid allowed_methods"})
    return {"result": "pass" if not errors else "fail", "request_count": len(value.get("requests", [])), "errors": errors}


def _ids_from_bundle(bundle: Path) -> tuple[set[str], set[str], str]:
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    models = {item["model_id"] for item in manifest.get("models", [])}
    rubrics: set[str] = set()
    for entry in manifest.get("inputs", {}).get("rubrics", []):
        for item in json.loads((bundle / entry["path"]).read_text(encoding="utf-8")):
            rubrics.add(item["id"])
    return models, rubrics, manifest["package_id"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path)
    parser.add_argument("base_bundle", type=Path)
    args = parser.parse_args(argv)
    value = json.loads(args.requests.read_text(encoding="utf-8"))
    models, rubrics, package_id = _ids_from_bundle(args.base_bundle)
    report = validate_requests(value, models, rubrics, package_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "pass" else 3


if __name__ == "__main__":
    sys.exit(main())
