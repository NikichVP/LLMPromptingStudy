#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
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
MODEL = "gpt-4o"
SYSTEM_PROMPT = """
You are an extremely strict Computer Science professional (professor-level) and an exam grader. Your top priority is maximal correctness, strict adherence to the problem statement, and format compliance.

Core behavior
- Treat the task statement as the only ground truth. Do not add unstated assumptions.
- If the question admits a conditional answer (e.g., “safe if X, unsafe if Y”), give the narrowest correct conditional answer rather than a blanket “always/never”.
- Write in a professional, concise style, but do not omit required details.

Method
1) Parse the prompt precisely:
   - Identify exactly what is being asked (e.g., choose one option, compute a value, provide a mechanism, “justify/explain”, etc.).
   - Identify required output format (IDs only, single letter, x/21, short paragraph, etc.). This is mandatory.

2) Choose a verification strategy (adaptive):
   A) If the problem is quantitative / algorithmic / proof-like (two genuinely different derivations exist):
      - Solve using TWO independent approaches (e.g., algebraic + invariants, constructive + contradiction, simulation + math).
      - Compare results. If they disagree, locate the exact assumption causing divergence and resolve it.
   B) If the problem is conceptual / factual / short-form (no meaningful independent derivations):
      - Solve it once carefully.
      - Then re-solve 2 more times (2–3 total passes) from scratch, each time:
        * re-reading the question,
        * checking definitions,
        * checking edge cases / exceptions,
        * checking that you answered what was asked (not something adjacent).
      - If any pass yields a different conclusion, reconcile and give the most statement-faithful answer.

3) Format & completeness gate (final pass):
   - If the prompt says “justify/explain/why/describe”, include a brief justification (typically 3–6 sentences unless the task specifies otherwise).
   - Include all key elements needed for full credit (e.g., if asked about password storage/verification, mention salt + one-way hash; if asked about page tables, mention the relevant access/translation conditions).
   - Do not add filler. Every sentence must earn points.

Output rules
- Output ONLY what the task explicitly requests, in exactly the required format.
- Be concise, but never at the expense of completeness or required justification.
- Do NOT reveal hidden chain-of-thought or step-by-step internal reasoning.
- If the task allows only one option, output only that option (and justification only if explicitly required).
"""
MAX_COMPLETION_TOKENS = 2048
REQUEST_DELAY_SEC = 0.2
TIMEOUT_SEC = 60
MAX_RETRIES = 3

INPUT_PATH = Path("questions_for_usage.json")
OUTPUT_PATH = Path("gpt_answers.json")

# Set integer (e.g. 10) to limit number of NEW sent queries per run, or None for all.
MAX_QUERIES: int | None = None


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _iter_questions(path: Path) -> Iterable[tuple[int | str, str]]:
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON in input file {path}: {e}") from e

    if not isinstance(data, list):
        raise RuntimeError(f"Input file {path} must contain a JSON array")

    for idx, obj in enumerate(data, 1):
        if not isinstance(obj, dict):
            _eprint(f"Skip item #{idx}: expected object")
            continue
        qid = obj.get("id")
        question = obj.get("question")
        if not isinstance(question, str):
            _eprint(f"Skip item #{idx}: missing question")
            continue
        if not isinstance(qid, (int, str)):
            _eprint(f"Skip item #{idx}: missing/invalid id")
            continue
        yield qid, question


def _id_key(qid: int | str) -> str:
    return str(qid)


def _load_existing_results(path: Path) -> tuple[list[dict[str, Any]], set[str], dict[str, int]]:
    if not path.exists():
        return [], set(), {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _eprint(f"Warning: could not read existing output {path}: {e}")
        return [], set(), {}

    if not isinstance(data, list):
        _eprint(f"Warning: existing output is not a list: {path}")
        return [], set(), {}

    existing_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    row_index_by_id: dict[str, int] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        row_answer = row.get("answer")
        if not isinstance(row_id, (int, str)) or not isinstance(row_answer, str):
            continue
        row_system_prompt = row.get("system_prompt")
        if not isinstance(row_system_prompt, str):
            row_system_prompt = SYSTEM_PROMPT
        normalized_row = {
            "id": row_id,
            "system_prompt": row_system_prompt,
            "answer": row_answer,
        }
        key = _id_key(row_id)

        existing_idx = row_index_by_id.get(key)
        if existing_idx is None:
            row_index_by_id[key] = len(existing_rows)
            existing_rows.append(normalized_row)
        else:
            existing_rows[existing_idx] = normalized_row

        if row_answer.strip():
            seen_ids.add(key)
        else:
            seen_ids.discard(key)

    return existing_rows, seen_ids, row_index_by_id


def _request_chatgpt(api_key: str, system_prompt: str, question: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(API_URL, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8")
            result = json.loads(body)
            choices = result.get("choices", [])
            if not choices:
                raise RuntimeError("Empty choices in response")
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
            if content.strip():
                return content
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"Model returned empty answer (finish_reason={finish_reason!r})"
                )
            sleep_for = min(2 ** attempt, 30)
            _eprint(
                "Empty model answer, retrying in "
                f"{sleep_for}s (attempt {attempt}/{MAX_RETRIES}, "
                f"finish_reason={finish_reason!r})"
            )
            time.sleep(sleep_for)
        except urllib.error.HTTPError as e:
            retryable = e.code in {429, 500, 502, 503, 504}
            body = e.read().decode("utf-8", errors="replace")
            if not retryable or attempt == MAX_RETRIES:
                raise RuntimeError(f"HTTP error {e.code}: {body}") from e
            sleep_for = min(2 ** attempt, 30)
            _eprint(
                f"HTTP {e.code}, retrying in {sleep_for}s "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(sleep_for)
        except (TimeoutError, socket.timeout) as e:
            if attempt == MAX_RETRIES:
                raise TimeoutError(f"Read timeout after {MAX_RETRIES} attempts") from e
            sleep_for = min(2 ** attempt, 30)
            _eprint(
                f"Timeout, retrying in {sleep_for}s "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(sleep_for)
        except urllib.error.URLError as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Network error: {e}") from e
            sleep_for = min(2 ** attempt, 30)
            _eprint(
                f"Network error, retrying in {sleep_for}s "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(sleep_for)

    raise RuntimeError("Request failed after retries")


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

    if MAX_QUERIES is not None and MAX_QUERIES < 0:
        _eprint("MAX_QUERIES must be >= 0 or None")
        return 1

    results, seen_ids, row_index_by_id = _load_existing_results(OUTPUT_PATH)
    processed_new = 0
    skipped_existing = 0

    if results:
        _eprint(f"Loaded {len(results)} existing answers from {OUTPUT_PATH}")

    for qid, question in _iter_questions(INPUT_PATH):
        if _id_key(qid) in seen_ids:
            skipped_existing += 1
            continue

        if MAX_QUERIES is not None and processed_new >= MAX_QUERIES:
            _eprint(f"Reached MAX_QUERIES={MAX_QUERIES}, stopping.")
            break

        try:
            answer = _request_chatgpt(api_key, SYSTEM_PROMPT, question)
        except TimeoutError:
            _eprint(f"Timeout on question {qid}, skipping to next.")
            continue
        result_row = {
            "id": qid,
            "system_prompt": SYSTEM_PROMPT,
            "answer": answer,
        }
        key = _id_key(qid)
        existing_idx = row_index_by_id.get(key)
        if existing_idx is None:
            row_index_by_id[key] = len(results)
            results.append(result_row)
        else:
            results[existing_idx] = result_row
        seen_ids.add(key)
        processed_new += 1
        _eprint(f"Processed new {processed_new}: {qid}")

        # Persist progress after each response, so partial work is not lost.
        _write_results(OUTPUT_PATH, results)

        if REQUEST_DELAY_SEC > 0:
            time.sleep(REQUEST_DELAY_SEC)

    _write_results(OUTPUT_PATH, results)
    _eprint(
        f"Finished. Added {processed_new} new answers, skipped {skipped_existing} existing. "
        f"Total saved: {len(results)} in {OUTPUT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
