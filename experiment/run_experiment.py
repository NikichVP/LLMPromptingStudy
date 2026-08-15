#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from prompts import PROMPTS

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o"
MAX_COMPLETION_TOKENS = 2048
TIMEOUT_SEC = 60
MAX_RETRIES = 3
REQUEST_DELAY_SEC = 0.2


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def load_questions(path: Path) -> Iterable[tuple[int | str, str]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Input must be a JSON array")

    for index, row in enumerate(data, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Item {index} must be an object")
        qid = row.get("id")
        question = row.get("question")
        if not isinstance(qid, (int, str)) or not isinstance(question, str):
            raise ValueError(f"Item {index} must contain 'id' and 'question'")
        yield qid, question


def request_answer(api_key: str, model: str, system_prompt: str, question: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }
    encoded = json.dumps(payload).encode("utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
        request = urllib.request.Request(API_URL, data=encoded, method="POST")
        request.add_header("Authorization", f"Bearer {api_key}")
        request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                body = json.loads(response.read().decode("utf-8"))
            choices = body.get("choices", [])
            if not choices:
                raise RuntimeError("API response contained no choices")
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                return content
            raise RuntimeError("Model returned an empty answer")
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {429, 500, 502, 503, 504}
            if not retryable or attempt == MAX_RETRIES:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Request failed after {MAX_RETRIES} attempts") from exc

        time.sleep(min(2**attempt, 30))

    raise RuntimeError("Request failed")


def load_existing(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.exists():
        return [], set()

    with path.open("r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError("Existing output must be a JSON array")

    seen = {
        str(row.get("id"))
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), (int, str))
    }
    return rows, seen


def save(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one PROMPT-RESEARCH condition")
    parser.add_argument("--condition", required=True, choices=sorted(PROMPTS))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-new", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        eprint("Missing OPENAI_API_KEY environment variable")
        return 1

    if args.max_new is not None and args.max_new < 0:
        eprint("--max-new must be >= 0")
        return 1

    prompt = PROMPTS[args.condition]
    results, seen = load_existing(args.output)
    added = 0

    for qid, question in load_questions(args.input):
        if str(qid) in seen:
            continue
        if args.max_new is not None and added >= args.max_new:
            break

        answer = request_answer(api_key, args.model, prompt, question)
        results.append({"id": qid, "system_prompt": prompt, "answer": answer})
        seen.add(str(qid))
        added += 1
        save(args.output, results)
        eprint(f"{args.condition}: saved {qid}")

        if REQUEST_DELAY_SEC > 0:
            time.sleep(REQUEST_DELAY_SEC)

    save(args.output, results)
    eprint(f"Finished. Added {added} answers; total {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
