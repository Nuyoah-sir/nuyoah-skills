#!/usr/bin/env python3
"""Render deny-by-default passive SVG with an auditable browser receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from artifact_integrity import sha256_file

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ALLOWED_ELEMENTS = {
    "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse", "line",
    "polyline", "polygon", "text", "tspan", "title", "desc", "clipPath", "mask", "pattern",
    "linearGradient", "radialGradient", "stop", "filter", "feGaussianBlur", "feOffset",
    "feColorMatrix", "feBlend", "feMerge", "feMergeNode", "marker", "image", "style",
}
ALLOWED_ATTRS = {
    "id", "class", "style", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry",
    "width", "height", "viewBox", "preserveAspectRatio", "d", "points", "transform", "fill",
    "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "opacity", "font-family", "font-size",
    "font-weight", "font-style", "text-anchor", "dominant-baseline", "offset", "stop-color",
    "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "href", "clip-path",
    "mask", "filter", "marker-start", "marker-mid", "marker-end", "xmlns",
}
_BAD_TEXT = re.compile(rb"<!DOCTYPE|<!ENTITY|@import|url\s*\(", re.IGNORECASE)
_PROCESSING_INSTRUCTION = re.compile(rb"<\?(?!xml(?:\s|\?>))", re.IGNORECASE)
_URLISH = re.compile(r"^(?:https?:|file:|ftp:|//|\\\\|data:)", re.IGNORECASE)
_CSS_PROPERTIES = {
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "opacity",
    "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline",
    "stop-color", "stop-opacity", "visibility", "display",
}


def _number(value: str | None) -> int | None:
    if not isinstance(value, str): return None
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)(?:px)?\s*", value)
    return max(1, round(float(match.group(1)))) if match else None


def _check_css_declarations(text: str) -> None:
    for declaration in text.split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            raise ValueError("malformed CSS declaration")
        prop, value = (part.strip() for part in declaration.split(":", 1))
        if prop not in _CSS_PROPERTIES or re.search(r"url\s*\(|expression\s*\(|javascript:|data:|[{}@]", value, re.IGNORECASE):
            raise ValueError("CSS is outside the presentation whitelist")


def _check_stylesheet(text: str) -> None:
    without_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    cursor = 0
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", without_comments):
        if without_comments[cursor:match.start()].strip():
            raise ValueError("malformed CSS stylesheet")
        selector = match.group(1).strip()
        if not selector or re.search(r"[^A-Za-z0-9_.#,:>+~*\-\s]", selector):
            raise ValueError("CSS selector is outside the presentation whitelist")
        _check_css_declarations(match.group(2))
        cursor = match.end()
    if without_comments[cursor:].strip():
        raise ValueError("malformed CSS stylesheet")


def inspect_passive_svg(data: bytes) -> dict[str, Any]:
    """Parse and whitelist one passive SVG; raise stable ``UNSAFE_SVG`` otherwise."""

    try:
        if len(data) > 16 * 1024 * 1024 or _BAD_TEXT.search(data) or _PROCESSING_INSTRUCTION.search(data):
            raise ValueError("prohibited declaration or CSS")
        root = ElementTree.fromstring(data)
        if root.tag != f"{{{SVG_NS}}}svg":
            raise ValueError("root element must be svg")
        ids: set[str] = set()
        references: list[str] = []
        for element in root.iter():
            if not element.tag.startswith(f"{{{SVG_NS}}}"):
                raise ValueError("unknown namespace")
            local = element.tag.split("}", 1)[1]
            if local not in ALLOWED_ELEMENTS:
                raise ValueError(f"element {local} is not allowed")
            for raw_name, value in element.attrib.items():
                if raw_name.startswith("{"):
                    namespace, name = raw_name[1:].split("}", 1)
                    if namespace != XLINK_NS or name != "href":
                        raise ValueError("unknown namespaced attribute")
                else:
                    name = raw_name
                if name.lower().startswith("on") or name not in ALLOWED_ATTRS:
                    raise ValueError(f"attribute {name} is not allowed")
                if name == "id": ids.add(value)
                if name == "style": _check_css_declarations(value)
                if "url(" in value.lower() or _URLISH.match(value.strip()):
                    raise ValueError("external or active URL")
                if name == "href":
                    if not value.startswith("#") or len(value) == 1:
                        raise ValueError("reference must target this SVG")
                    references.append(value[1:])
            if local == "style":
                _check_stylesheet(element.text or "")
        if any(reference not in ids for reference in references):
            raise ValueError("reference target is missing")
        width, height = _number(root.get("width")), _number(root.get("height"))
        if width is None or height is None:
            view_box = root.get("viewBox", "").replace(",", " ").split()
            if len(view_box) == 4:
                width, height = max(1, round(float(view_box[2]))), max(1, round(float(view_box[3])))
        if width is None or height is None or width > 16384 or height > 16384 or width * height > 64_000_000:
            raise ValueError("unbounded SVG dimensions")
        return {
            "adapter_class": "passive_static_only", "width": width, "height": height,
            "safety_findings": {"well_formed": True, "presentation_whitelist": True, "same_document_references": True},
        }
    except (ElementTree.ParseError, UnicodeError, ValueError, OverflowError) as exc:
        if str(exc).startswith("UNSAFE_SVG"):
            raise
        raise ValueError(f"UNSAFE_SVG: {exc}") from exc


def _find_renderer(candidate: str | Path | None) -> Path:
    candidates = []
    if candidate is not None:
        candidates.append(Path(candidate))
    else:
        for command in ("msedge", "chrome", "google-chrome"):
            found = shutil.which(command)
            if found: candidates.append(Path(found))
        candidates.extend(Path(path) for path in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ))
    for path in candidates:
        if path.is_absolute() and path.is_file() and any(name in path.name.lower() for name in ("msedge", "chrome")):
            return path.resolve()
    raise ValueError("NO_QUALIFIED_RENDERER: absolute Edge or Chrome executable not found")


def render_media(
    input_path: str | Path,
    output_path: str | Path,
    *,
    renderer_executable: str | Path | None = None,
    media_id: str,
    role: str,
    bindings: list[dict[str, Any]],
    parent_media_id: str | None = None,
    crop: dict[str, int] | None = None,
) -> Path:
    """Render passive SVG only. This adapter is explicitly not an active-code sandbox."""

    source, output = Path(input_path), Path(output_path)
    if not isinstance(media_id, str) or not media_id or "/" in media_id or "\\" in media_id or media_id in {".", ".."}:
        raise ValueError("MEDIA_ID_INVALID")
    if role not in {"candidate_full", "candidate_crop"}:
        raise ValueError("RENDER_ROLE_INVALID")
    if source.suffix.lower() != ".svg":
        raise ValueError("NO_QUALIFIED_RENDERER: v2.0.0 supports passive SVG only, not active HTML/apps")
    source_digest = sha256_file(source)
    inspection = inspect_passive_svg(source.read_bytes())
    executable = _find_renderer(renderer_executable)
    executable_digest = sha256_file(executable)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Render output already exists: {output}")
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="vibe-passive-svg-") as temporary:
        profile = Path(temporary) / "profile"
        staged_input = Path(temporary) / "input.svg"
        staged_output = Path(temporary) / "output.png"
        shutil.copyfile(source, staged_input)
        if sha256_file(staged_input) != source_digest or sha256_file(source) != source_digest:
            raise ValueError("MEDIA_SOURCE_CHANGED: SVG changed while staging")
        argv = [str(executable), "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", f"--user-data-dir={profile}", f"--screenshot={staged_output}",
                f"--window-size={inspection['width']},{inspection['height']}", staged_input.resolve().as_uri()]
        version_run = subprocess.run([str(executable), "--version"], capture_output=True, check=False)
        if version_run.returncode != 0:
            raise ValueError("RENDERER_VERSION_FAILED")
        run = subprocess.run(argv, capture_output=True, check=False)
        if run.returncode != 0 or not staged_output.is_file():
            raise ValueError(f"RENDER_FAILED: browser exit={run.returncode}")
        from register_media import inspect_image
        output_info = inspect_image(staged_output)
        if output_info["mime"] != "image/png":
            raise ValueError("RENDER_FAILED: renderer output is not PNG")
        staged_publish = output.with_name(f".{output.name}.tmp")
        try:
            shutil.copyfile(staged_output, staged_publish)
            os.replace(staged_publish, output)
        finally:
            staged_publish.unlink(missing_ok=True)
        normalized = [item.replace(str(profile), "<TEMP_PROFILE>").replace(str(staged_input), "<INPUT_FILE>").replace(staged_input.resolve().as_uri(), "<INPUT_FILE>").replace(str(staged_output), "<OUTPUT_FILE>") for item in argv]
    if sha256_file(executable) != executable_digest:
        output.unlink(missing_ok=True)
        raise ValueError("RENDERER_CHANGED: executable changed during rendering")
    ended = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt = {
        "receipt_source": "render_media", "adapter_class": "passive_static_only",
        "adapter_security_scope": "passive SVG renderer; not an active-code sandbox",
        "media_id": media_id, "role": role, "bindings": bindings,
        "renderer_executable": str(executable), "renderer_sha256": executable_digest,
        "renderer_version": (version_run.stdout or version_run.stderr).decode("utf-8", "replace").strip(),
        "argv": normalized, "started_at": started, "ended_at": ended, "exit_code": run.returncode,
        "stdout_sha256": hashlib.sha256(run.stdout).hexdigest(), "stderr_sha256": hashlib.sha256(run.stderr).hexdigest(),
        "input_sha256": source_digest, "output_sha256": sha256_file(output),
        "output_width": output_info["width"], "output_height": output_info["height"],
        "safety_findings": inspection["safety_findings"],
    }
    if parent_media_id is not None:
        receipt["parent_media_id"] = parent_media_id
    if crop is not None:
        receipt["crop"] = crop
    receipt_path = output.with_suffix(output.suffix + ".render-receipt.json")
    receipt_sidecar = receipt_path.with_suffix(receipt_path.suffix + ".sha256")
    if receipt_path.exists() or receipt_sidecar.exists():
        output.unlink(missing_ok=True)
        raise FileExistsError("Renderer receipt output already exists")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt_sidecar.write_text(f"{sha256_file(receipt_path)}  {receipt_path.name}\n", encoding="utf-8")
    return receipt_path


__all__ = ["inspect_passive_svg", "render_media"]
