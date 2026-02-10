#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

# =========================
# Config (edit only here)
# =========================
API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-5-mini"
MAX_COMPLETION_TOKENS = 2048
TIMEOUT_SEC = 60
REQUEST_DELAY_SEC = 0.2

INPUT_PATH = Path("qa_extracted/qa_pairs_clean.jsonl")

# Maximum number of NEW GPT requests per run (None = no limit).
MAX_GPT_REQUESTS: int | None = 100

OUTPUT_PATH = Path("gpt_questions_checker_results.json")

SYSTEM_PROMPT = (
    "You are a strict evaluator of question quality for Q&A datasets. "
    "Given a question and its expected answer, output ONLY one numeric score from 0 to 1. "
    "Scoring goal: measure whether the question is clear and self-sufficient enough to produce "
    "the expected answer correctly. "
    "Rules: "
    "1) If any critical information required to answer correctly is missing (missing data, missing "
    "constraints, missing options/context/definitions, or unresolved references to absent figures/tables), "
    "return exactly 0. "
    "2) Otherwise start from 1 and reduce for each issue: ambiguity, vague wording, possible multiple "
    "interpretations, weak constraints, or confusing phrasing. "
    "3) Do not reward style; score only answerability/clarity. "
    "4) Do not output explanation, JSON, words, or symbols; output only a single decimal number like 0, 0.35, 1."
)


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _iter_questions(path: Path) -> Iterable[tuple[str, str, str]]:
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                _eprint(f"Skip malformed JSON in input at line {line_num}")
                continue

            qid = obj.get("id")
            question = obj.get("question")
            if not isinstance(qid, str) or not isinstance(question, str):
                _eprint(f"Skip input line {line_num}: missing id/question")
                continue

            suggested_answer = obj.get("answer")
            if not isinstance(suggested_answer, str):
                suggested_answer = ""
            yield qid, question, suggested_answer


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip()


def _load_existing_results(path: Path) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    if not path.exists():
        return [], set(), set()

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _eprint(f"Warning: could not read existing output {path}: {e}")
        return [], set(), set()

    if not isinstance(data, list):
        _eprint(f"Warning: existing output is not a list: {path}")
        return [], set(), set()

    existing_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()

    for row in data:
        if not isinstance(row, dict):
            continue
        existing_rows.append(row)

        row_id = row.get("id")
        if isinstance(row_id, str):
            seen_ids.add(row_id)

        row_question = row.get("question")
        if isinstance(row_question, str):
            seen_questions.add(_normalize_question(row_question))

    return existing_rows, seen_ids, seen_questions


def _request_score(api_key: str, question: str, suggested_answer: str) -> float:
    user_prompt = (
        "Question:\n"
        f"{question}\n\n"
        "Expected answer:\n"
        f"{suggested_answer}\n\n"
        "Return only the score."
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e}") from e

    result = json.loads(body)
    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError("Empty choices in API response")

    finish_reason = choices[0].get("finish_reason")
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text_value = part.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        content = "".join(parts)
    if not isinstance(content, str):
        content = ""

    m = re.search(r"\b(?:0(?:\.\d+)?|1(?:\.0+)?)\b", content.strip())
    if not m:
        raise RuntimeError(
            f"Model returned non-score: {content!r} (finish_reason={finish_reason!r})"
        )

    score = float(m.group(0))
    if score < 0 or score > 1:
        raise RuntimeError(f"Score out of range: {score}")
    return score


def _write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def main() -> int:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        _eprint("Missing OPENAI_API_KEY environment variable")
        return 1

    if not INPUT_PATH.exists():
        _eprint(f"Input not found: {INPUT_PATH}")
        return 1

    if MAX_GPT_REQUESTS is not None and MAX_GPT_REQUESTS < 0:
        _eprint("MAX_GPT_REQUESTS must be >= 0 or None")
        return 1

    results, seen_ids, seen_questions = _load_existing_results(OUTPUT_PATH)
    processed_new = 0
    skipped_existing = 0

    if results:
        _eprint(f"Loaded {len(results)} existing results from {OUTPUT_PATH}")

    for qid, question, suggested_answer in _iter_questions(INPUT_PATH):
        normalized_question = _normalize_question(question)
        if qid in seen_ids or normalized_question in seen_questions:
            skipped_existing += 1
            continue

        if MAX_GPT_REQUESTS is not None and processed_new >= MAX_GPT_REQUESTS:
            _eprint(f"Reached MAX_GPT_REQUESTS={MAX_GPT_REQUESTS}, stopping.")
            break

        score = _request_score(api_key, question, suggested_answer)
        result_row = {
            "id": qid,
            "question": question,
            "suggested_answer": suggested_answer,
            "score": score,
        }
        results.append(result_row)
        seen_ids.add(qid)
        seen_questions.add(normalized_question)
        processed_new += 1

        _write_results(OUTPUT_PATH, results)
        _eprint(f"Processed new {processed_new}: {qid} -> {score}")

        if REQUEST_DELAY_SEC > 0:
            time.sleep(REQUEST_DELAY_SEC)

    _write_results(OUTPUT_PATH, results)
    _eprint(
        f"Finished. Added {processed_new} new records, skipped {skipped_existing} existing. "
        f"Total saved: {len(results)} in {OUTPUT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
