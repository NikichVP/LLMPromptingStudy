#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


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


def _jsonl_dump(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


_GATE_KIND_RE = re.compile(
    r"^GATE(?:\s+CS)?\s+(?P<year>\d{4})\s+Set\s+(?P<set>\d+)\s+(?P<kind>Question\s+Paper|Answer\s+Key)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GatePairKey:
    year: int
    set_no: int


def _infer_gate_kind(stem: str) -> tuple[GatePairKey, str] | None:
    m = _GATE_KIND_RE.match(stem.strip())
    if not m:
        return None
    key = GatePairKey(year=int(m.group("year")), set_no=int(m.group("set")))
    kind = m.group("kind").lower()
    if "question" in kind:
        return key, "question"
    if "answer" in kind:
        return key, "answer"
    return None


def _normalize_qid(qid: Any) -> str:
    if qid is None:
        raise ValueError("Missing question_id")
    s = str(qid).strip()
    if not s:
        raise ValueError("Empty question_id")
    if not s.isdigit():
        raise ValueError(f"Non-numeric question_id: {qid!r}")
    n = int(s)
    if n <= 0:
        raise ValueError(f"Invalid question_id: {qid!r}")
    return str(n)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_question_rows_named(source_name: str, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        qid = _normalize_qid(r.get("question_id"))
        _require(qid not in by_id, f"Duplicate question_id={qid} in {source_name}")

        qt = (r.get("question_text") or "").strip()
        _require(qt != "", f"Missing question_text for question_id={qid} in {source_name}")

        at = r.get("answer_text")
        _require(
            at is None or str(at).strip() == "",
            f"Unexpected answer_text in question paper for question_id={qid} in {source_name}",
        )

        by_id[qid] = r
    return by_id


def _validate_question_rows(path: Path, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _validate_question_rows_named(path.name, rows)


def _validate_answer_rows_named(source_name: str, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        qid = _normalize_qid(r.get("question_id"))
        _require(qid not in by_id, f"Duplicate question_id={qid} in {source_name}")

        at = (r.get("answer_text") or "").strip()
        _require(at != "", f"Missing answer_text for question_id={qid} in {source_name}")

        qt = r.get("question_text")
        _require(
            qt is None or str(qt).strip() == "",
            f"Unexpected question_text in answer key for question_id={qid} in {source_name}",
        )

        by_id[qid] = r
    return by_id


def _validate_answer_rows(path: Path, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _validate_answer_rows_named(path.name, rows)


def merge_gate_question_answer_rows(
    question_rows: list[dict[str, Any]],
    answer_rows: list[dict[str, Any]],
    *,
    question_name: str,
    answer_name: str,
) -> list[dict[str, Any]]:
    q_by_id = _validate_question_rows_named(question_name, question_rows)
    a_by_id = _validate_answer_rows_named(answer_name, answer_rows)

    q_ids = set(q_by_id)
    a_ids = set(a_by_id)
    missing_in_answers = sorted(q_ids - a_ids, key=int)
    missing_in_questions = sorted(a_ids - q_ids, key=int)
    _require(not missing_in_answers, f"Answer key missing question_id(s): {missing_in_answers}")
    _require(not missing_in_questions, f"Question paper missing question_id(s): {missing_in_questions}")

    merged: list[dict[str, Any]] = []
    for order, qid in enumerate(sorted(q_ids, key=int), start=1):
        q = q_by_id[qid]
        a = a_by_id[qid]
        out: dict[str, Any] = {
            "parser": "gate_qa_merge_v1",
            "order_in_source": order,
            "question_id": qid,
            "question_text": q.get("question_text"),
            "answer_text": a.get("answer_text"),
            "question_source_txt": q.get("source_txt"),
            "answer_source_txt": a.get("source_txt"),
            "question_source_pdf": q.get("source_pdf"),
            "question_source_sha256": q.get("source_sha256"),
            "question_page_count": q.get("page_count"),
            "answer_source_pdf": a.get("source_pdf"),
            "answer_source_sha256": a.get("source_sha256"),
            "answer_page_count": a.get("page_count"),
            "question_page_start": q.get("page_start"),
            "question_page_end": q.get("page_end"),
            "question_line_start": q.get("line_start"),
            "question_line_end": q.get("line_end"),
            "answer_page_start": a.get("page_start"),
            "answer_page_end": a.get("page_end"),
            "answer_line_start": a.get("line_start"),
            "answer_line_end": a.get("line_end"),
            "question_raw_text": q.get("raw_text"),
            "answer_raw_text": a.get("raw_text"),
        }

        for k in ("gate_session", "gate_qtype", "gate_section", "gate_marks"):
            if k in a:
                out[k] = a.get(k)
        merged.append(out)

    for r in merged:
        _require((r.get("question_text") or "").strip() != "", f"Merged record missing question_text: {r.get('question_id')}")
        _require((r.get("answer_text") or "").strip() != "", f"Merged record missing answer_text: {r.get('question_id')}")

    return merged


def _merge_pair(question_path: Path, answer_path: Path) -> list[dict[str, Any]]:
    q_rows = _jsonl_load(question_path)
    a_rows = _jsonl_load(answer_path)

    q_by_id = _validate_question_rows(question_path, q_rows)
    a_by_id = _validate_answer_rows(answer_path, a_rows)

    q_ids = set(q_by_id)
    a_ids = set(a_by_id)
    missing_in_answers = sorted(q_ids - a_ids, key=int)
    missing_in_questions = sorted(a_ids - q_ids, key=int)
    _require(not missing_in_answers, f"Answer key missing question_id(s): {missing_in_answers}")
    _require(not missing_in_questions, f"Question paper missing question_id(s): {missing_in_questions}")

    merged: list[dict[str, Any]] = []
    for order, qid in enumerate(sorted(q_ids, key=int), start=1):
        q = q_by_id[qid]
        a = a_by_id[qid]
        out: dict[str, Any] = {
            "parser": "gate_qa_merge_v1",
            "order_in_source": order,
            "question_id": qid,
            "question_text": q.get("question_text"),
            "answer_text": a.get("answer_text"),
            "question_source_txt": str(question_path.resolve()),
            "answer_source_txt": str(answer_path.resolve()),
            "question_source_pdf": q.get("source_pdf"),
            "question_source_sha256": q.get("source_sha256"),
            "question_page_count": q.get("page_count"),
            "answer_source_pdf": a.get("source_pdf"),
            "answer_source_sha256": a.get("source_sha256"),
            "answer_page_count": a.get("page_count"),
            "question_page_start": q.get("page_start"),
            "question_page_end": q.get("page_end"),
            "question_line_start": q.get("line_start"),
            "question_line_end": q.get("line_end"),
            "answer_page_start": a.get("page_start"),
            "answer_page_end": a.get("page_end"),
            "answer_line_start": a.get("line_start"),
            "answer_line_end": a.get("line_end"),
            "question_raw_text": q.get("raw_text"),
            "answer_raw_text": a.get("raw_text"),
        }

        for k in ("gate_session", "gate_qtype", "gate_section", "gate_marks"):
            if k in a:
                out[k] = a.get(k)
        merged.append(out)

    # Final sanity: everything has both Q and A.
    for r in merged:
        _require((r.get("question_text") or "").strip() != "", f"Merged record missing question_text: {r.get('question_id')}")
        _require((r.get("answer_text") or "").strip() != "", f"Merged record missing answer_text: {r.get('question_id')}")

    return merged


def merge_gate_pairs_from_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    grouped: dict[GatePairKey, dict[str, list[dict[str, Any]]]] = {}
    for r in records:
        src_pdf = r.get("source_pdf")
        if not src_pdf:
            continue
        inferred = _infer_gate_kind(Path(str(src_pdf)).stem)
        if inferred is None:
            continue
        key, kind = inferred
        grouped.setdefault(key, {}).setdefault(kind, []).append(r)

    merged_all: list[dict[str, Any]] = []
    used_pdfs: set[str] = set()
    for key in sorted(grouped, key=lambda k: (k.year, k.set_no)):
        kinds = grouped[key]
        if "question" not in kinds or "answer" not in kinds:
            continue
        q_rows = kinds["question"]
        a_rows = kinds["answer"]
        q_pdfs = {str(r.get("source_pdf")) for r in q_rows if r.get("source_pdf")}
        a_pdfs = {str(r.get("source_pdf")) for r in a_rows if r.get("source_pdf")}
        _require(len(q_pdfs) == 1, f"Expected one question paper PDF for {key}, got: {sorted(q_pdfs)}")
        _require(len(a_pdfs) == 1, f"Expected one answer key PDF for {key}, got: {sorted(a_pdfs)}")
        merged = merge_gate_question_answer_rows(
            q_rows,
            a_rows,
            question_name=next(iter(q_pdfs)),
            answer_name=next(iter(a_pdfs)),
        )
        merged_all.extend(merged)
        used_pdfs.update(q_pdfs)
        used_pdfs.update(a_pdfs)

    return merged_all, used_pdfs


def _auto_discover_pairs(by_source_dir: Path) -> list[tuple[GatePairKey, Path, Path]]:
    questions: dict[GatePairKey, Path] = {}
    answers: dict[GatePairKey, Path] = {}

    for p in sorted(by_source_dir.glob("*.jsonl")):
        inferred = _infer_gate_kind(p.stem)
        if inferred is None:
            continue
        key, kind = inferred
        if kind == "question":
            questions[key] = p
        elif kind == "answer":
            answers[key] = p

    common = sorted(set(questions) & set(answers), key=lambda k: (k.year, k.set_no))
    _require(common, f"No GATE Question Paper + Answer Key pairs found in {by_source_dir}")

    pairs: list[tuple[GatePairKey, Path, Path]] = []
    for key in common:
        pairs.append((key, questions[key], answers[key]))
    return pairs


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Merge GATE Question Paper + Answer Key records into unified QA JSONL (by question_id)."
    )
    p.add_argument("--by-source-dir", default="qa_extracted/by_source", help="Directory with per-source JSONL files.")
    p.add_argument("--out", default="qa_extracted/gate_qa_merged.jsonl", help="Output JSONL path.")
    p.add_argument("--question", help="Explicit Question Paper JSONL path (disables auto-discovery).")
    p.add_argument("--answer", help="Explicit Answer Key JSONL path (disables auto-discovery).")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    out_path = Path(args.out).expanduser().resolve()

    merged_all: list[dict[str, Any]] = []
    if args.question or args.answer:
        if not (args.question and args.answer):
            _eprint("Both --question and --answer must be provided together.")
            return 2
        q = Path(args.question).expanduser().resolve()
        a = Path(args.answer).expanduser().resolve()
        merged_all = _merge_pair(q, a)
        _eprint(f"[OK] merged {len(merged_all)} record(s): {q.name} + {a.name}")
    else:
        by_source_dir = Path(args.by_source_dir).expanduser().resolve()
        pairs = _auto_discover_pairs(by_source_dir)
        for key, q, a in pairs:
            merged = _merge_pair(q, a)
            merged_all.extend(merged)
            _eprint(f"[OK] {key.year} set {key.set_no}: merged {len(merged)} record(s)")

    _jsonl_dump(out_path, merged_all)
    _eprint(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
