#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import qa_extractor


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", errors="strict")
    os.replace(tmp, path)


def _jsonl_load(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on {path}:{i}: {e}") from e
    return out


def _jsonl_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _norm(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


_ROMAN_HEADING_RE = re.compile(r"^\s*[IVX]{1,6}\s{2,}[A-Za-z].*$")
_BANKER_MERGE_RE = re.compile(r"(\d)(P\d)\b")
_GRADING_CUE_RE = re.compile(
    r"(?i)\b(?:"
    r"we\s+(?:\w+\s+){0,3}(?:deduct(?:ed)?|subtract(?:ed)?|award(?:ed)?|accept(?:ed)?)"
    r"|did\s+not\s+award"
    r"|did\s+not\s+accept"
    r"|partial\s+credit"
    r"|the\s+correct\s+answer\s+was\s+worth"
    r"|unacceptable\s+answers"
    r")\b"
)
_BANKER_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+P(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)


def _normalize_unicode(s: str) -> str:
    # NFKC fixes most PDF ligatures like "ﬁ" -> "fi".
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    s = s.replace("“", '"').replace("”", '"').replace("„", '"').replace("‟", '"')
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    return s


def _join_hyphenated_linebreaks(s: str) -> str:
    # Merge line-break hyphenations.
    # - identi-\n cally -> identically  (hyphenation artifact)
    # - third-\n level -> third-level   (real hyphen kept)
    keep_hyphen_left = {
        "non",
        "pre",
        "post",
        "re",
        "co",
        "multi",
        "anti",
        "micro",
        "macro",
        "pseudo",
        "quasi",
        "semi",
        "sub",
        "super",
        "over",
        "under",
        "inter",
        "intra",
        "trans",
        "ultra",
        "self",
        "cross",
        "high",
        "low",
        "mid",
        "first",
        "second",
        "third",
    }

    def repl(m: re.Match[str]) -> str:
        left = m.group(1)
        right = m.group(2)
        if left.lower() in keep_hyphen_left:
            return f"{left}-{right}"
        return f"{left}{right}"

    return re.sub(r"(\w+)-\n\s*(\w+)", repl, s)


def _fix_common_pdf_artifacts(s: str) -> str:
    # 2, 000 -> 2,000
    s = re.sub(r"(\d),\s+(\d{3})\b", r"\1,\2", s)
    # oﬀa -> offa (after NFKC) -> off a
    s = re.sub(r"\boffa\b", "off a", s)
    return s


def _strip_section_headings(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if _ROMAN_HEADING_RE.match(line):
            continue
        out.append(line)
    return out


def _strip_grading_notes(answer: str) -> str:
    # Remove grading rubric text while trying to keep the real answer (tables/solutions).
    lines = answer.splitlines()
    if not lines:
        return ""

    # 1) Remove trailing grading tails: once grading starts, it's usually not part of the answer.
    tail_cut = None
    for i, line in enumerate(lines):
        m = _GRADING_CUE_RE.search(line)
        if not m:
            continue
        tail_cut = i
        # Keep any useful prefix on the same line before grading starts.
        prefix = line[: m.start()].rstrip()
        kept = lines[:i]
        if prefix.strip():
            kept = kept + [prefix]
        # Remove dangling "We" fragments caused by line wraps like:
        # "... operation. We\n did not award ..."
        while kept and kept[-1].strip().lower() == "we":
            kept.pop()
        if kept:
            kept[-1] = re.sub(r"\bWe\s*$", "", kept[-1]).rstrip()

        cand = "\n".join(kept).rstrip()
        if cand.strip():
            return cand
        break

    # 2) Remove leading grading prefixes like "We deducted ..." if followed by real content.
    #    Skip whole grading paragraph(s) and keep the remaining content.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and _GRADING_CUE_RE.search(lines[i]):
        j = i
        while j < len(lines) and (lines[j].strip() or _GRADING_CUE_RE.search(lines[j])):
            j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        remainder = "\n".join(lines[j:]).rstrip()
        return remainder.strip()

    return "\n".join(lines).rstrip().strip()


_POINTS_PREFIX_RE = re.compile(
    r"(?im)^\s*(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"points?\s+for\s+correct\s+answer\s*:?\s*"
)
_PTS_PAREN_RE = re.compile(r"\s*\([^)]*\b(?:pts?|points?)\b[^)]*\)")


def _strip_points_annotations(answer: str) -> str:
    # Remove rubric-like point annotations but keep the actual content.
    s = _POINTS_PREFIX_RE.sub("", answer)
    s = _PTS_PAREN_RE.sub("", s)
    s = re.sub(r",\s*,", ", ", s)
    s = re.sub(r"\s+\.", ".", s)

    cleaned_lines: list[str] = []
    for line in s.splitlines():
        if (
            "|" in line
            or line.lstrip().startswith("Reference")
            or "Page #" in line
            or "Scheduler" in line
            or "Avg Turnaround Time" in line
        ):
            cleaned_lines.append(line.rstrip())
            continue
        cleaned_lines.append(re.sub(r"(?<=\S) {2,}(?=\S)", " ", line).rstrip())

    return "\n".join(cleaned_lines).strip()


def _fix_specific_records(record_id: str, *, question: str, answer: str) -> tuple[str, str]:
    # CS140 Autumn 2007 Solutions::4 answer sometimes ends with the next section header.
    if record_id == "CS140 Autumn 2007 Solutions::4":
        answer = "\n".join(
            line for line in answer.splitlines() if line.strip() not in {"II  Security", "II Security"}
        ).rstrip()

    # FA13 exponent formatting got flattened (e.g., 211 -> 2^11).
    if record_id == "FA13 MT1 Solutions::4.b.i":
        # The actual solution text is embedded in question_text for this one; answer_text is grading notes.
        m = re.search(r"(?m)^\s*Since\b", question)
        if m:
            sol = question[m.start() :].strip()
            question = question[: m.start()].rstrip()
            answer = sol
        answer = re.sub(r"\b211\b(?=\s+pages?\b)", "2^11", answer)
        answer = re.sub(r"\b213\b(?=\s+bytes\b)", "2^13", answer)
        answer = re.sub(r"\b224\b(?=\s+bytes\b)", "2^24", answer)
        answer = re.sub(r"\b211\s*\*\s*213\b", "2^11 * 2^13", answer)
        answer = re.sub(r"\b235\s*bytes\b", "2^35 bytes", answer)
        answer = re.sub(r"\b246\s*bytes\b", "2^46 bytes", answer)
        answer = re.sub(r"\b211\s*\*\s*2\^13\b", "2^11 * 2^13", answer)
        answer = re.sub(r"\b2\^11\s*\*\s*213\b", "2^11 * 2^13", answer)

    if record_id == "FA13 MT1 Solutions::4.b.ii":
        answer = re.sub(r"\b233\b(?=\s+bytes\b)", "2^33", answer)

    if record_id == "FA13 MT1 Solutions::3.c":
        # The FIFO filled table is embedded in question_text; answer_text is grading notes.
        m = re.search(r"(?m)^\s*Reference\b", question)
        if m:
            table = question[m.start() :].strip()
            question = question[: m.start()].rstrip()
            answer = table

    if record_id == "FA13 MT1 Solutions::4.a":
        # Solution is embedded in question_text (derivation); answer_text is grading notes.
        m = re.search(r"(?m)^\s*Effective Access Time\b", question)
        if m:
            deriv = question[m.start() :].strip()
            question = question[: m.start()].rstrip()
            # Prefer the final hit ratio if present.
            h = None
            for line in deriv.splitlines()[::-1]:
                mm = re.search(r"\bH\s*=\s*([0-9]+/[0-9]+)\b", line)
                if mm:
                    h = f"H = {mm.group(1)}"
                    break
            answer = h or deriv

    if record_id == "FA13 MT1 Solutions::4.b.iv":
        # Solution is embedded in question_text; answer_text is grading notes.
        m = re.search(r"(?m)^\s*Six\b", question)
        if m:
            sol = question[m.start() :].strip()
            question = question[: m.start()].rstrip()
            answer = sol
        answer = re.sub(r"\b211\b", "2^11", answer)

    # Banker snapshot table has merged process labels like "0P1".
    if record_id in {
        "FA13 MT1 Solutions::2.a.i",
        "FA13 MT1 Solutions::2.a.ii",
        "FA13 MT1 Solutions::2.a.iii",
    }:
        question = _BANKER_MERGE_RE.sub(r"\1 \2", question)
        lines = question.splitlines()
        start = next((i for i, l in enumerate(lines) if "Currently Available Resources" in l), None)
        end = None
        if start is not None:
            for i in range(start, len(lines)):
                if re.search(r"\bP5\b", lines[i]):
                    end = i
        if start is not None and end is not None and end > start:
            available: list[str] | None = None
            rows: list[tuple[str, list[str], list[str], list[str]]] = []
            for l in lines[start : end + 1]:
                if available is None and "P" not in l and re.match(r"^\s*\d", l):
                    nums = re.findall(r"\d+", l)
                    if len(nums) == 4:
                        available = nums
                m = _BANKER_ROW_RE.match(l)
                if m:
                    need = [m.group(1), m.group(2), m.group(3), m.group(4)]
                    p = f"P{m.group(5)}"
                    alloc = [m.group(6), m.group(7), m.group(8), m.group(9)]
                    maxv = [m.group(10), m.group(11), m.group(12), m.group(13)]
                    rows.append((p, alloc, maxv, need))

            if rows:
                table: list[str] = []
                if available:
                    table.append(f"Currently Available Resources (R1 R2 R3 R4): {' '.join(available)}")
                table.append("Process | Alloc (R1 R2 R3 R4) | Max (R1 R2 R3 R4) | Need (R1 R2 R3 R4)")
                for p, alloc, maxv, need in rows:
                    table.append(f"{p:<7}| {' '.join(alloc):<18} | {' '.join(maxv):<16} | {' '.join(need)}")
                question = "\n".join(lines[:start] + table + lines[end + 1 :]).strip()

    # Incomplete in the source extraction (Approach #2 is empty).
    if record_id == "FA13 MT1 Solutions::2.b":
        answer = re.sub(r"(?im)^\s*Approach\s*#2\s*:\s*$", "", answer).strip()

    return question, answer


def _clean_text(s: str, *, is_answer: bool) -> str:
    s = _normalize_unicode(s)
    s = _join_hyphenated_linebreaks(s)
    s = _fix_common_pdf_artifacts(s)

    lines: list[str] = []
    for line in s.splitlines():
        if qa_extractor._is_noise_line(line):
            continue
        lines.append(line.rstrip("\n").rstrip())

    lines = _strip_section_headings(lines)

    out = "\n".join(lines).strip()
    # Collapse huge blank runs but preserve structure.
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    if is_answer:
        out = _strip_grading_notes(out).strip()
        out = _strip_points_annotations(out)
    return out


def _record_base_path(r: dict[str, Any]) -> str:
    for k in ("source_pdf", "question_source_pdf", "source_txt", "question_source_txt"):
        v = r.get(k)
        if v:
            return str(v)
    return "unknown"


def _make_id(r: dict[str, Any]) -> str:
    base = _record_base_path(r)
    stem = Path(base).stem
    qid = _norm(r.get("question_id")) or "unknown"
    return f"{stem}::{qid}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export only complete Q+A rows into a minimal JSONL file.")
    p.add_argument("--in", dest="in_path", default="qa_extracted/qa_records_merged.jsonl", help="Input JSONL path.")
    p.add_argument("--out", dest="out_path", default="qa_extracted/qa_pairs_clean.jsonl", help="Output JSONL path.")
    p.add_argument("--overwrite", action="store_true", help="Overwrite output file if present.")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    in_path = Path(args.in_path).expanduser().resolve()
    out_path = Path(args.out_path).expanduser().resolve()

    if not in_path.exists():
        _eprint(f"Input not found: {in_path}")
        return 2
    if out_path.exists() and not args.overwrite:
        _eprint(f"Refusing to overwrite: {out_path} (use --overwrite)")
        return 2

    rows = _jsonl_load(in_path)
    out_rows: list[dict[str, str]] = []
    for r in rows:
        record_id = _make_id(r)
        q = _clean_text(_norm(r.get("question_text")), is_answer=False)
        a = _clean_text(_norm(r.get("answer_text")), is_answer=True)
        q, a = _fix_specific_records(record_id, question=q, answer=a)
        if not q or not a:
            continue
        # Drop known-incomplete rows that are missing required content in the extracted source.
        if record_id == "FA13 MT1 Solutions::2.b":
            continue
        out_rows.append({"answer": a, "id": _make_id(r), "question": q})

    _atomic_write_text(out_path, "".join(_jsonl_dumps(r) + "\n" for r in out_rows))
    _eprint(f"Wrote: {out_path} ({len(out_rows)} row(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
