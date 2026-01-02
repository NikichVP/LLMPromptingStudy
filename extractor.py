#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w.\- ]+", "_", stem, flags=re.UNICODE).strip()
    return stem or "document"


def _iter_pdfs(paths: list[Path], *, recursive: bool) -> list[Path]:
    pdfs: list[Path] = []
    for p in paths:
        if p.is_dir():
            if recursive:
                pdfs.extend(sorted(p.rglob("*.pdf")))
            else:
                pdfs.extend(sorted(p.glob("*.pdf")))
        else:
            if p.suffix.lower() == ".pdf":
                pdfs.append(p)
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in pdfs:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(rp)
    return unique


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", errors="strict")
    os.replace(tmp, path)


def _json_default(obj: Any) -> Any:
    # Make best-effort JSON for PyMuPDF types (and anything else unexpected).
    for xy in (("x", "y"), ("X", "Y")):
        if all(hasattr(obj, k) for k in xy):
            return {xy[0].lower(): float(getattr(obj, xy[0])), xy[1].lower(): float(getattr(obj, xy[1]))}
    rect_attrs = ("x0", "y0", "x1", "y1")
    if all(hasattr(obj, k) for k in rect_attrs):
        return {k: float(getattr(obj, k)) for k in rect_attrs}
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        # Rare in our outputs, but keep it representable.
        return {"__bytes_b64__": b64encode(obj).decode("ascii")}
    return str(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)


@dataclass(frozen=True)
class OcrConfig:
    mode: str  # "off" | "auto" | "force"
    dpi: int
    lang: str


def _have_tesseract() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_image_to_text(image_path: Path, *, lang: str) -> str:
    if not _have_tesseract():
        raise RuntimeError("tesseract not found on PATH")
    # Output to stdout ("-") keeps things in-memory and avoids extra files.
    proc = subprocess.run(
        ["tesseract", str(image_path), "-", "-l", lang, "--dpi", "300"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"tesseract failed (code={proc.returncode})")
    return proc.stdout


def _format_kv(title: str, value: Any) -> str:
    if value is None:
        return f"{title}: <none>"
    if isinstance(value, (dict, list, tuple)):
        return f"{title}:\n{_json_dumps(value)}"
    return f"{title}: {value}"


def _normalize_newlines(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if not s.endswith("\n"):
        s += "\n"
    return s


def _extract_page_spans(rawdict: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for block in rawdict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text is None or text == "":
                    continue
                spans.append(
                    {
                        "bbox": span.get("bbox"),
                        "size": span.get("size"),
                        "font": span.get("font"),
                        "color": span.get("color"),
                        "flags": span.get("flags"),
                        "text": text,
                    }
                )
    return spans


def extract_pdf_to_txt(
    pdf_path: Path,
    *,
    out_dir: Path,
    overwrite: bool,
    include_spans: bool,
    extract_images: bool,
    ocr: OcrConfig,
    password: str | None,
) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []

    try:
        import fitz  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency: PyMuPDF.\n"
            "Install with (recommended): python3.11 -m pip install pymupdf\n"
            "If you're on Python 3.14 and install fails, use Python 3.11 instead.\n"
            f"Import error: {e}"
        ) from e

    pdf_stem = _safe_stem(pdf_path.name)
    out_txt = out_dir / f"{pdf_stem}.txt"
    if out_txt.exists() and not overwrite:
        warnings.append(f"Skip (already exists): {out_txt}")
        return None, warnings

    sha256 = _sha256_file(pdf_path)
    file_stat = pdf_path.stat()

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Failed to open PDF: {pdf_path} ({e})") from e

    try:
        if getattr(doc, "needs_pass", False):
            if not password:
                raise RuntimeError("PDF is password-protected; pass --password")
            if not doc.authenticate(password):
                raise RuntimeError("Invalid --password for PDF")

        meta = doc.metadata or {}
        toc = doc.get_toc(simple=False)  # may include page + title + level

        now = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
        header: list[str] = []
        header.append("=== PDF EXTRACTION REPORT ===")
        header.append(_format_kv("Source", str(pdf_path)))
        header.append(_format_kv("SHA256", sha256))
        header.append(_format_kv("Bytes", file_stat.st_size))
        header.append(_format_kv("ExtractedAtUTC", now))
        header.append(_format_kv("PageCount", doc.page_count))
        header.append(_format_kv("Metadata", meta))
        header.append(_format_kv("TOC", toc))
        header.append("")

        images_dir: Path | None = None
        if extract_images:
            images_dir = out_dir / f"{pdf_stem}_images"
            images_dir.mkdir(parents=True, exist_ok=True)

        parts: list[str] = [_normalize_newlines("\n".join(header))]

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_number = page_index + 1

            page_info = {
                "page": page_number,
                "rotation": int(page.rotation),
                "rect": [float(page.rect.x0), float(page.rect.y0), float(page.rect.x1), float(page.rect.y1)],
            }
            parts.append(_normalize_newlines(f"\n\n--- PAGE {page_number}/{doc.page_count} ---\n{_json_dumps(page_info)}\n"))

            extracted_text = page.get_text("text", sort=True) or ""
            extracted_text = extracted_text.strip("\n")
            parts.append(_normalize_newlines("[TEXT]\n" + (extracted_text if extracted_text else "<empty>") + "\n"))

            rawdict: dict[str, Any] | None = None
            try:
                rawdict = page.get_text("rawdict")  # contains coordinates for spans & images
            except Exception as e:
                warnings.append(f"rawdict failed on page {page_number}: {e}")

            if include_spans and rawdict is not None:
                spans = _extract_page_spans(rawdict)
                parts.append(_normalize_newlines("[SPANS]\n" + (_json_dumps(spans) if spans else "[]") + "\n"))

            if extract_images:
                try:
                    images = page.get_images(full=True) or []
                except Exception as e:
                    images = []
                    warnings.append(f"get_images failed on page {page_number}: {e}")

                extracted_images: list[dict[str, Any]] = []
                for img_i, img in enumerate(images, start=1):
                    # PyMuPDF returns tuples; xref is first element.
                    xref = img[0]
                    try:
                        info = doc.extract_image(xref)
                        img_bytes: bytes = info.get("image", b"")
                        ext = info.get("ext", "bin")
                        out_name = f"page_{page_number:04d}_img_{img_i:03d}_xref_{xref}.{ext}"
                        out_path = (images_dir or out_dir) / out_name
                        out_path.write_bytes(img_bytes)
                        extracted_images.append(
                            {
                                "page": page_number,
                                "xref": xref,
                                "file": str(out_path),
                                "ext": ext,
                                "width": info.get("width"),
                                "height": info.get("height"),
                                "colorspace": info.get("colorspace"),
                                "bpc": info.get("bpc"),
                                "size_bytes": len(img_bytes),
                            }
                        )
                    except Exception as e:
                        warnings.append(f"image extract failed (page={page_number}, xref={xref}): {e}")
                parts.append(_normalize_newlines("[IMAGES]\n" + _json_dumps(extracted_images) + "\n"))

            # Links & annotations often matter for “all information”.
            try:
                links = page.get_links() or []
            except Exception as e:
                links = []
                warnings.append(f"get_links failed on page {page_number}: {e}")
            parts.append(_normalize_newlines("[LINKS]\n" + _json_dumps(links) + "\n"))

            annots_out: list[dict[str, Any]] = []
            try:
                annot = page.first_annot
                while annot:
                    annots_out.append(
                        {
                            "type": getattr(annot, "type", None),
                            "info": getattr(annot, "info", None),
                            "rect": list(getattr(annot, "rect", [])) if getattr(annot, "rect", None) else None,
                        }
                    )
                    annot = annot.next
            except Exception as e:
                warnings.append(f"annotations failed on page {page_number}: {e}")
            parts.append(_normalize_newlines("[ANNOTATIONS]\n" + _json_dumps(annots_out) + "\n"))

            # OCR: only attempt if asked, and either forced or text appears empty.
            should_ocr = ocr.mode == "force" or (ocr.mode == "auto" and extracted_text.strip() == "")
            if should_ocr:
                if not _have_tesseract():
                    warnings.append(
                        f"OCR skipped on page {page_number}: tesseract not installed "
                        "(install with: brew install tesseract)"
                    )
                    parts.append(_normalize_newlines("[OCR]\n<skipped: tesseract not installed>\n"))
                else:
                    zoom = max(1.0, ocr.dpi / 72.0)
                    try:
                        with tempfile.TemporaryDirectory(prefix="pdf_ocr_") as td:
                            tmp_png = Path(td) / f"page_{page_number}.png"
                            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                            pix.save(str(tmp_png))
                            ocr_text = _ocr_image_to_text(tmp_png, lang=ocr.lang).strip("\n")
                            parts.append(
                                _normalize_newlines("[OCR]\n" + (ocr_text if ocr_text else "<empty>") + "\n")
                            )
                    except Exception as e:
                        warnings.append(f"OCR failed on page {page_number}: {e}")
                        parts.append(_normalize_newlines("[OCR]\n<failed>\n"))

        content = "".join(parts)
        _atomic_write_text(out_txt, content)
        return out_txt, warnings
    finally:
        doc.close()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract text (and other info) from PDFs into .txt files (with optional OCR)."
    )
    p.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="PDF files or directories (default: current directory).",
    )
    p.add_argument("--out-dir", default="extracted_txt", help="Output directory (default: extracted_txt).")
    p.add_argument("--recursive", action="store_true", help="Recurse into subdirectories when paths are dirs.")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing .txt outputs.")
    p.add_argument(
        "--include-spans",
        action="store_true",
        help="Include JSON dump of text spans + coordinates for maximum fidelity.",
    )
    p.add_argument(
        "--images",
        action="store_true",
        help="Extract embedded images to <out-dir>/<pdf>_images/ and reference them in txt.",
    )
    p.add_argument(
        "--ocr",
        choices=["off", "auto", "force"],
        default="auto",
        help="OCR mode: off, auto (only if page text is empty), or force (every page). Default: auto.",
    )
    p.add_argument("--ocr-dpi", type=int, default=300, help="OCR render DPI (default: 300).")
    p.add_argument("--ocr-lang", default="eng", help="tesseract language (default: eng).")
    p.add_argument("--password", default=None, help="Password for encrypted PDFs (if needed).")
    p.add_argument(
        "--write-manifest",
        action="store_true",
        help="Write extracted_txt/manifest.json summarizing outputs and warnings.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    in_paths = [Path(p).expanduser() for p in args.paths]
    out_dir = Path(args.out_dir).expanduser().resolve()
    pdfs = _iter_pdfs(in_paths, recursive=bool(args.recursive))
    if not pdfs:
        _eprint("No PDFs found.")
        return 2

    ocr_cfg = OcrConfig(mode=str(args.ocr), dpi=int(args.ocr_dpi), lang=str(args.ocr_lang))

    _eprint(f"Found {len(pdfs)} PDF(s). Output: {out_dir}")
    failures = 0
    manifest: list[dict[str, Any]] = []
    for pdf in pdfs:
        try:
            out_path, warnings = extract_pdf_to_txt(
                pdf,
                out_dir=out_dir,
                overwrite=bool(args.overwrite),
                include_spans=bool(args.include_spans),
                extract_images=bool(args.images),
                ocr=ocr_cfg,
                password=args.password,
            )
            if out_path is None:
                _eprint(f"[SKIP] {pdf.name}")
            else:
                _eprint(f"[OK]   {pdf.name} -> {out_path.name}")
            for w in warnings:
                _eprint(f"  - {w}")
            manifest.append(
                {
                    "pdf": str(pdf),
                    "out_txt": str(out_path) if out_path else None,
                    "warnings": warnings,
                }
            )
        except Exception as e:
            failures += 1
            _eprint(f"[FAIL] {pdf} :: {e}")
            manifest.append(
                {
                    "pdf": str(pdf),
                    "out_txt": None,
                    "warnings": [str(e)],
                    "status": "fail",
                }
            )

    if args.write_manifest:
        manifest_path = out_dir / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(_json_dumps(manifest), encoding="utf-8", errors="strict")
        _eprint(f"Wrote manifest: {manifest_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
