#!/usr/bin/env python3
"""Deterministically flatten CodeBuddy conversation messages without semantic guessing."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _message(raw) -> tuple[str, str, bool]:
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return raw, "unknown", False
    if not isinstance(value, dict):
        return str(value), "unknown", False
    role = str(value.get("role", "unknown"))
    content = value.get("content", "")
    pieces = []
    has_tool = False
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                pieces.append(str(item))
            elif item.get("type") == "text":
                pieces.append(str(item.get("text", "")))
            elif item.get("type") in {"tool-call", "tool_call"}:
                has_tool = True
                pieces.append(json.dumps({"tool": item.get("toolName") or item.get("name"), "args": item.get("args")}, ensure_ascii=False, sort_keys=True))
            else:
                pieces.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    else:
        pieces.append(str(content))
    return "\n".join(piece for piece in pieces if piece), role, has_tool


def summarize_conversation_bytes(raw: bytes) -> dict:
    value = json.loads(raw.decode("utf-8"))
    conversations = value.get("data", {}).get("conversations")
    if not isinstance(conversations, list):
        raise ValueError("Expected data.conversations to be an array")
    events = []
    request_count = 0
    for conversation in conversations:
        requests = conversation.get("requests", []) if isinstance(conversation, dict) else None
        if not isinstance(requests, list):
            raise ValueError("Every conversation.requests must be an array")
        for round_number, request in enumerate(requests, 1):
            request_count += 1
            messages = request.get("messages", []) if isinstance(request, dict) else None
            if not isinstance(messages, list):
                raise ValueError("Every request.messages must be an array")
            for outer in messages:
                if not isinstance(outer, dict):
                    raise ValueError("Every message must be an object")
                excerpt, inner_role, has_tool = _message(outer.get("message", ""))
                role = str(outer.get("role") or inner_role)
                event_type = "assistant_tool_call" if role == "assistant" and has_tool else f"{role}_message"
                index = len(events)
                events.append({"msg_ref": f"MSG-{index:06d}", "flat_msg_index": index, "round": round_number, "role": role, "excerpt": excerpt, "event_type": event_type})
    return {
        "schema_version": "1.0.0",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "key_events": events,
        "structural_counts": {"conversation_count": len(conversations), "request_count": request_count, "message_count": len(events)},
        "statistics": {"debug_count": {"value": None, "unavailable_reason": "Requires semantic classification; deterministic collector does not guess debug events."}},
    }


def summarize_conversation(source_path: str | Path, output_path: str | Path, task_root: str | Path | None = None, model_metadata: str | Path | None = None, blob_dir: str | Path | None = None, bundle_root: str | Path | None = None) -> dict:
    source = Path(source_path).resolve()
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if model_metadata is not None:
        if task_root is None:
            raise ValueError("task_root is required with model_metadata")
        metadata = json.loads(Path(model_metadata).read_text(encoding="utf-8"))
        root = Path(task_root).resolve()
        try:
            label = source.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("Conversation source is outside task_root") from exc
        if metadata.get("conversation_source_path") != label:
            raise ValueError(f"Conversation source {label!r} does not match frozen model binding {metadata.get('conversation_source_path')!r}")
    raw = source.read_bytes()
    result = summarize_conversation_bytes(raw)
    if (blob_dir is None) != (bundle_root is None):
        raise ValueError("blob_dir and bundle_root must be provided together")
    if blob_dir is not None:
        blobs = Path(blob_dir).resolve()
        bundle = Path(bundle_root).resolve()
        try:
            blobs.relative_to(bundle)
        except ValueError as exc:
            raise ValueError("blob_dir must be inside bundle_root") from exc
        blob = blobs / f"{result['source_sha256']}.json"
        if blob.exists() and blob.read_bytes() != raw:
            raise ValueError("Existing conversation blob does not match source")
        blob.parent.mkdir(parents=True, exist_ok=True)
        if not blob.exists():
            blob.write_bytes(raw)
        result["source_blob_path"] = blob.relative_to(bundle).as_posix()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--task-root", type=Path)
    parser.add_argument("--model-metadata", type=Path)
    parser.add_argument("--blob-dir", type=Path)
    parser.add_argument("--bundle-root", type=Path)
    args = parser.parse_args(argv)
    try:
        summarize_conversation(args.source, args.output, args.task_root, args.model_metadata, args.blob_dir, args.bundle_root)
    except (ValueError, FileExistsError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
