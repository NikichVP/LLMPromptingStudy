#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

# =========================
# Config (edit only here)
# =========================
API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"  # selected GPT model
SYSTEM_PROMPT = "You are a helpful assistant."
TEMPERATURE = 0.2
MAX_TOKENS = 1024
REQUEST_DELAY_SEC = 0.2
TIMEOUT_SEC = 60
MAX_RETRIES = 5

INPUT_PATH = Path("qa_extracted/qa_pairs_clean.jsonl")
OUTPUT_PATH = Path("queries_to_gpt_answers.json")

# Set integer (e.g. 10) to limit number of sent queries, or None for all.
MAX_QUERIES: int | None = None


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


def _request_chatgpt(api_key: str, system_prompt: str, question: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
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
            message = choices[0].get("message", {})
            content = message.get("content")
            if not isinstance(content, str):
                raise RuntimeError("Missing message content in response")
            return content
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


def _write_results(path: Path, rows: list[dict[str, str]]) -> None:
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

    results: list[dict[str, str]] = []
    processed = 0

    for qid, question, suggested_answer in _iter_questions(INPUT_PATH):
        if MAX_QUERIES is not None and processed >= MAX_QUERIES:
            _eprint(f"Reached MAX_QUERIES={MAX_QUERIES}, stopping.")
            break

        answer = _request_chatgpt(api_key, SYSTEM_PROMPT, question)
        results.append(
            {
                "id": qid,
                "system_prompt": SYSTEM_PROMPT,
                "question": question,
                "suggested_answer": suggested_answer,
                "answer": answer,
            }
        )
        processed += 1
        _eprint(f"Processed {processed}: {qid}")

        # Persist progress after each response, so partial work is not lost.
        _write_results(OUTPUT_PATH, results)

        if REQUEST_DELAY_SEC > 0:
            time.sleep(REQUEST_DELAY_SEC)

    _write_results(OUTPUT_PATH, results)
    _eprint(f"Finished. Saved {processed} records to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
