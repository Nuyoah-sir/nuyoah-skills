#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把《Coding Agent 人评标准》.docx 转成高保真 Markdown（含图片、表格、自动编号）。

用法（Windows 用 py 启动器）：
    py docx_to_markdown.py "Coding Agent 人评标准 V 2.1.docx" \
        --out "Coding Agent 人评标准 V2.1.md" \
        --assets-dir "assets/人评标准V2.1" \
        --assets-url "assets/人评标准V2.1"

设计要点：
- 按文档流顺序遍历段落与表格，图片插在原位置，不做摘要或重排；
- 还原 Word 自动编号（numPr/numbering.xml），中文数字章节号按 Word 实际渲染结果补齐；
- 表格转 GFM 表格，单元格内多段文字用 <br> 连接；
- 超链接、加粗、斜体做轻量还原。

依赖：python-docx（`py -m pip install --no-cache-dir python-docx`）。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- numbering

def parse_numbering(docx_path: Path):
    """返回 {numId: (abstractId, {ilvl: {'fmt':.., 'text':..}})} 与 abstract 级别的起始值。"""
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/numbering.xml").decode("utf-8")
    abstracts = {}
    for m in re.finditer(
        r'<w:abstractNum [^>]*w:abstractNumId="(\d+)"(.*?)</w:abstractNum>', xml, re.S
    ):
        aid, body = m.group(1), m.group(2)
        levels = {}
        for lm in re.finditer(r'<w:lvl [^>]*w:ilvl="(\d+)"[^>]*>(.*?)</w:lvl>', body, re.S):
            ilvl, lb = int(lm.group(1)), lm.group(2)
            fmt = re.search(r'<w:numFmt w:val="([^"]+)"', lb)
            txt = re.search(r'<w:lvlText w:val="([^"]*)"', lb)
            start = re.search(r'<w:start w:val="([^"]+)"', lb)
            levels[ilvl] = {
                "fmt": fmt.group(1) if fmt else "decimal",
                "text": txt.group(1) if txt else "%1.",
                "start": int(start.group(1)) if start else 1,
            }
        abstracts[aid] = levels
    num_map = {}
    for m in re.finditer(
        r'<w:num w:numId="(\d+)"[^>]*>\s*<w:abstractNumId w:val="(\d+)"', xml
    ):
        num_map[m.group(1)] = m.group(2)
    return {nid: (aid, abstracts.get(aid, {})) for nid, aid in num_map.items()}


ROMAN = [
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
]
CN_DIGITS = "零一二三四五六七八九"


def to_roman(n: int) -> str:
    out = []
    for value, sym in ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def to_chinese(n: int) -> str:
    if n <= 10:
        return "十" if n == 10 else CN_DIGITS[n]
    if n < 20:
        return "十" + CN_DIGITS[n % 10]
    if n < 100:
        return CN_DIGITS[n // 10] + "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def format_counter(n: int, fmt: str) -> str:
    if fmt in ("decimal", "decimalZero", "chineseCounting", "chineseCountingThousand", "japaneseCounting"):
        return to_chinese(n) if fmt.startswith(("chinese", "japanese")) else str(n)
    if fmt == "lowerLetter":
        return chr(ord("a") + (n - 1) % 26)
    if fmt == "upperLetter":
        return chr(ord("A") + (n - 1) % 26)
    if fmt == "lowerRoman":
        return to_roman(n)
    if fmt == "upperRoman":
        return to_roman(n).upper()
    return str(n)


class Numbering:
    """按 Word 语义推进编号计数器。"""

    def __init__(self, num_map):
        self.num_map = num_map
        self.counters: dict[str, dict[int, int]] = {}
        self.heading_counter = 0

    def resolve(self, num_pr):
        """返回 (abstract, levels, ilvl) 或 None。"""
        if num_pr is None:
            return None
        num_id_el = num_pr.find(qn("w:numId"))
        if num_id_el is None:
            return None
        nid = num_id_el.get(qn("w:val"))
        if nid not in self.num_map:
            return None
        ilvl_el = num_pr.find(qn("w:ilvl"))
        ilvl = int(ilvl_el.get(qn("w:val"))) if ilvl_el is not None else 0
        abstract, levels = self.num_map[nid]
        return abstract, levels, ilvl

    def advance(self, num_pr):
        """推进计数器，返回 (abstract, levels, ilvl)。"""
        resolved = self.resolve(num_pr)
        if resolved is None:
            return None
        abstract, levels, ilvl = resolved
        counter = self.counters.setdefault(abstract, {})
        for deeper in [k for k in counter if k > ilvl]:
            del counter[deeper]
        counter[ilvl] = counter.get(ilvl, levels.get(ilvl, {}).get("start", 1) - 1) + 1
        return resolved

    def marker_text(self, resolved, override: int | None = None) -> tuple[str, bool]:
        abstract, levels, ilvl = resolved
        counter = self.counters.setdefault(abstract, {})
        level = levels.get(ilvl) or {"fmt": "decimal", "text": f"%{ilvl + 1}."}
        text = level["text"] or f"%{ilvl + 1}."
        for lvl in range(1, 10):
            if f"%{lvl}" in text:
                value = override if (override is not None and lvl - 1 == ilvl) else counter.get(lvl - 1)
                if value is None:
                    lvl_def = levels.get(lvl - 1) or {"fmt": "decimal", "start": 1}
                    value = lvl_def.get("start", 1)
                text = text.replace(
                    f"%{lvl}", format_counter(value, (levels.get(lvl - 1) or {}).get("fmt", "decimal"))
                )
        return text, level["fmt"] == "bullet"

    def para_number(self, num_pr):
        """推进并返回 (marker, is_bullet)；无编号返回 (None, False)。"""
        resolved = self.advance(num_pr)
        if resolved is None:
            return None, False
        return self.marker_text(resolved)

    def list_marker(self, num_pr, override: int | None = None) -> str | None:
        """只渲染当前计数器的编号文本（用于按标题计数器渲染的章节号）。"""
        resolved = self.resolve(num_pr)
        if resolved is None:
            return None
        text, _ = self.marker_text(resolved, override=override)
        return text

    def heading_number(self) -> int:
        self.heading_counter += 1
        return self.heading_counter


# ---------------------------------------------------------------- inline runs

def image_markdown(rid, rels, media_out: list[tuple[str, str]], assets_url: str) -> str:
    target = rels[rid].target_ref if rid and rid in rels else None
    if not target:
        return ""
    filename = Path(target).name
    media_out.append((rid, filename))
    return f"![{Path(filename).stem}]({assets_url}/{filename})"


def run_pieces(run_el, rels, media_out, assets_url) -> list[tuple[str, bool, bool]]:
    """把 w:r 拆成 (文本, 加粗, 斜体) 片段；图片单独成段，避免被强调符包住。"""
    rpr = run_el.find(qn("w:rPr"))
    bold = rpr is not None and rpr.find(qn("w:b")) is not None
    italic = rpr is not None and rpr.find(qn("w:i")) is not None
    pieces: list[tuple[str, bool, bool]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            pieces.append(("".join(buffer), bold, italic))
            buffer.clear()

    for node in run_el.iter():
        name = local_name(node.tag)
        if name == "t":
            buffer.append(node.text or "")
        elif name == "tab":
            buffer.append("\t")
        elif name in ("br", "cr"):
            buffer.append("<br>")
        elif name == "blip":
            flush()
            md = image_markdown(node.get(qn("r:embed")) or node.get(qn("r:link")), rels, media_out, assets_url)
            if md:
                pieces.append((md, False, False))
    flush()
    return pieces


def paragraph_pieces(p_el, rels, media_out, assets_url) -> list[tuple[str, bool, bool]]:
    pieces: list[tuple[str, bool, bool]] = []
    for node in p_el.iterchildren():
        name = local_name(node.tag)
        if name == "r":
            pieces.extend(run_pieces(node, rels, media_out, assets_url))
        elif name == "hyperlink":
            rid = node.get(qn("r:id"))
            inner = "".join(
                text
                for run in node.findall(qn("w:r"))
                for text, _, _ in run_pieces(run, rels, media_out, assets_url)
            )
            url = rels[rid].target_ref if rid and rid in rels else ""
            if inner:
                pieces.append((f"[{inner}]({url})" if url else inner, False, False))
    # 合并相邻同样式的片段
    merged: list[tuple[str, bool, bool]] = []
    for text, bold, italic in pieces:
        if not text:
            continue
        if merged and not text.startswith("![") and merged[-1][1:] == (bold, italic) and not merged[-1][0].endswith(")"):
            merged[-1] = (merged[-1][0] + text, bold, italic)
        else:
            merged.append((text, bold, italic))
    return merged


IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def render_pieces(pieces, emphasis: bool = True) -> str:
    """渲染行内片段。

    Word 里同一段的加粗/斜体常常只覆盖半句话，逐片段加标记会写出 `***甲****：乙*` 这类
    畸形嵌套。这里统一取整段口径：整段只有一种样式时按整段加标记，混用时不加标记，
    保证 Markdown 一定可读、可渲染。
    """
    if not emphasis:
        return "".join(text for text, _, _ in pieces).strip()
    styles = {(bold, italic) for text, bold, italic in pieces
              if text.strip() and not IMAGE_RE.fullmatch(text.strip())}
    if len(styles) != 1:
        return "".join(text for text, _, _ in pieces).strip()
    bold, italic = next(iter(styles))
    if not bold and not italic:
        return "".join(text for text, _, _ in pieces).strip()
    rendered: list[str] = []
    for text, piece_bold, piece_italic in pieces:
        if text.strip() and not IMAGE_RE.fullmatch(text.strip()):
            if bold and italic:
                text = f"***{text}***"
            elif bold:
                text = f"**{text}**"
            else:
                text = f"*{text}*"
        rendered.append(text)
    return "".join(rendered).strip()


def paragraph_markdown(par: Paragraph, rels, media_out, assets_url, emphasis: bool = True) -> str:
    return render_pieces(paragraph_pieces(par._p, rels, media_out, assets_url), emphasis)


def table_markdown(table: Table, rels, media_out, assets_url) -> str:
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            chunks = [
                paragraph_markdown(Paragraph(p._p, table), rels, media_out, assets_url)
                for p in cell.paragraphs
            ]
            cells.append("<br>".join(c for c in chunks if c))
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
    return "\n".join(lines)


def convert(src: Path, out: Path, assets_dir: Path, assets_url: str, header: str | None) -> dict:
    doc = Document(str(src))
    num_map = parse_numbering(src)
    numbering = Numbering(num_map)
    rels = doc.part.rels
    media_out: list[tuple[str, str]] = []
    blocks: list[str] = []

    for child in doc.element.body.iterchildren():
        name = local_name(child.tag)
        if name == "p":
            par = Paragraph(child, doc)
            style = par.style.name or ""
            p_pr = par._p.pPr
            num_pr = p_pr.numPr if p_pr is not None else None
            if style.startswith("Heading") or style == "Title":
                level = 1 if style == "Title" else int(re.search(r"(\d+)", style).group(1)) + 1
                text = paragraph_markdown(par, rels, media_out, assets_url, emphasis=False)
                plain = IMAGE_RE.sub("", text).strip()
                if not plain:
                    if text:
                        blocks.append(text)
                    continue
                if level == 2:
                    number = numbering.heading_number()
                    if num_pr is not None and num_pr.find(qn("w:numId")) is not None:
                        marker = numbering.list_marker(num_pr, override=number)
                        if marker:
                            text = f"{marker}{text}"
                blocks.append("#" * min(level, 6) + " " + text)
                continue
            text = paragraph_markdown(par, rels, media_out, assets_url)
            if not text:
                continue
            if num_pr is not None and num_pr.find(qn("w:numId")) is not None:
                marker, is_bullet = numbering.para_number(num_pr)
                ilvl_el = num_pr.find(qn("w:ilvl"))
                depth = int(ilvl_el.get(qn("w:val"))) if ilvl_el is not None else 0
                prefix = "- " if is_bullet else f"{marker} "
                blocks.append("    " * depth + prefix + text)
            else:
                blocks.append(text)
        elif name == "tbl":
            md = table_markdown(Table(child, doc), rels, media_out, assets_url)
            if md:
                blocks.append(md)

    asset_map = {}
    if media_out:
        assets_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            names = set(z.namelist())
            for rid, filename in media_out:
                if filename in asset_map:
                    continue
                target = rels[rid].target_ref
                member = "word/" + target if not target.startswith("word/") else target
                if member not in names:
                    candidates = [n for n in names if n.endswith("/" + filename)]
                    if not candidates:
                        continue
                    member = candidates[0]
                (assets_dir / filename).write_bytes(z.read(member))
                asset_map[filename] = member

    body = "\n\n".join(blocks).rstrip() + "\n"
    if header:
        body = header.rstrip() + "\n\n" + body
    out.write_text(body, encoding="utf-8")
    return {
        "blocks": len(blocks),
        "images_referenced": len(media_out),
        "images_unique": len(asset_map),
        "chars": len(body),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="docx -> markdown (fidelity first)")
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assets-dir", required=True)
    ap.add_argument("--assets-url", required=True)
    ap.add_argument("--header-file", default=None)
    args = ap.parse_args()

    header = Path(args.header_file).read_text(encoding="utf-8") if args.header_file else None
    stats = convert(Path(args.src), Path(args.out), Path(args.assets_dir), args.assets_url, header)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
