#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", errors="strict")
    os.replace(tmp, path)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def _jsonl_dumps(obj: Any) -> str:
    # JSON Lines requires 1 JSON object per line.
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iter_txts(in_dir: Path) -> list[Path]:
    txts = sorted(p for p in in_dir.glob("*.txt") if p.is_file())
    return txts


def _extract_header_fields(txt: str) -> dict[str, str | None]:
    source_pdf = None
    sha256 = None
    page_count = None
    for line in txt.splitlines():
        if line.startswith("--- PAGE "):
            break
        if line.startswith("Source: "):
            source_pdf = line.removeprefix("Source: ").strip() or None
        elif line.startswith("SHA256: "):
            sha256 = line.removeprefix("SHA256: ").strip() or None
        elif line.startswith("PageCount: "):
            page_count = line.removeprefix("PageCount: ").strip() or None
    out: dict[str, str | None] = {"source_pdf": source_pdf, "source_sha256": sha256}
    if page_count is not None:
        out["page_count"] = page_count
    return out


@dataclass(frozen=True)
class TextLine:
    page: int
    line_no: int  # 1-based within combined text stream
    text: str


def _iter_pages_text(txt: str) -> Iterator[tuple[int, str]]:
    # Our extractor format:
    # --- PAGE N/M ---
    # {json}
    # [TEXT]
    # ...
    # [SPANS]
    # ...
    page_re = re.compile(r"^--- PAGE (\d+)/(\d+) ---\s*$")
    lines = txt.splitlines()
    i = 0
    while i < len(lines):
        m = page_re.match(lines[i].strip())
        if not m:
            i += 1
            continue
        page_no = int(m.group(1))
        i += 1
        # Skip JSON block until [TEXT]
        while i < len(lines) and lines[i].strip() != "[TEXT]":
            i += 1
        if i >= len(lines):
            break
        # Consume [TEXT]
        i += 1
        text_lines: list[str] = []
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("[") and s.endswith("]") and s.isupper():
                break
            if lines[i].startswith("--- PAGE "):
                break
            text_lines.append(lines[i])
            i += 1
        yield page_no, "\n".join(text_lines).rstrip("\n")


def _is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^Organising Institute:.*Page \d+ of \d+$", s):
        return True
    if re.match(r"^Page \d+ of \d+$", s):
        return True
    if re.match(r"^Page\s+\d+\s*/\s*\d+\s*$", s):
        return True
    if re.match(r"^Page\s+\d+/\d+\s*$", s):
        return True
    # Common academic exam headers/footers.
    if re.match(r"^\d*\s*6\.006\s+Final\s+Name\b.*$", s):
        return True
    if re.match(r"^\d+\s+6\.006\s+Final\s+Name\b.*$", s):
        return True
    if re.match(r"^\s*6\.006\s+Introduction to Algorithms\s*$", s):
        return True
    if re.match(r"^\s*Spring\s+2020\s*$", s):
        return True
    if re.match(r"^\s*MIT OpenCourseWare\s*$", s):
        return True
    if re.match(r"^\s*https?://\S+\s*$", s):
        return True
    if re.match(r"^\s*For information about citing these materials\b.*$", s):
        return True
    if re.match(r"^\s*SCRATCH PAPER\s+\d+\.\s+DO NOT REMOVE FROM THE EXAM\.\s*$", s):
        return True
    if re.match(r"^\s*SCRATCH PAPER\s+\d+\.\s*$", s):
        return True
    if re.match(r"^\s*DO NOT REMOVE FROM THE EXAM\.\s*$", s):
        return True
    if re.match(r"^\s*You can use this paper to write a longer solution\b.*$", s):
        return True
    if re.match(
        r"^[\"“]?\s*Continued on S\d+[\"”]?\s+on the problem statement(?:'|’)?s page\.\s*$",
        s,
    ):
        return True
    if re.match(r"^\s*CS\s*162\b.*Midterm\s+Exam\b.*$", s):
        return True
    if re.match(r"^\s*Solutions\s+NAME:\s*_+\s*$", s, flags=re.IGNORECASE):
        return True
    if re.match(r"^\s*CS61BL,.*Midterm.*\b(?:Exam|Solutions)\b.*\d+\s*$", s):
        return True
    return False


def _build_line_stream(txt: str) -> list[TextLine]:
    out: list[TextLine] = []
    line_no = 0
    for page_no, page_text in _iter_pages_text(txt):
        for line in page_text.splitlines():
            if _is_noise_line(line):
                continue
            line_no += 1
            out.append(TextLine(page=page_no, line_no=line_no, text=line))
        # Preserve a page boundary gap as an empty line.
        line_no += 1
        out.append(TextLine(page=page_no, line_no=line_no, text=""))
    return out


_GATE_Q_RANGE_RE = re.compile(r"^\s*Q\.\s*\d+\s*[–-]\s*Q\.\s*\d+\b")
_GATE_Q_START_RE = re.compile(r"^\s*Q\.\s*(\d{1,3})\b")
_PROBLEM_WORD_START_RE = re.compile(r"^\s*Problem\s+(\d{1,3})\s*[\.:]\s*", re.IGNORECASE)
_QUESTION_WORD_START_RE = re.compile(r"^\s*Question\s+(\d{1,3})\b", re.IGNORECASE)
_QUESTION_CONTINUED_RE = re.compile(r"^\s*Question\s+(\d{1,3})\b.*continued", re.IGNORECASE)
_NUM_PAREN_START_RE = re.compile(r"^\s*\((\d{1,3})\)\s*")
_NUM_DOT_START_RE = re.compile(r"^\s*(\d{1,3})\.\s+")
_NUM_TITLE_START_RE = re.compile(r"^\s*(\d{1,3})\s{2,}(\S.+)$")
_POINTS_RE = re.compile(r"(?i)\b\d+\s*(?:points?|pts?)\b")


def _question_start_id(line: str) -> str | None:
    if _QUESTION_CONTINUED_RE.match(line):
        return None
    if _GATE_Q_RANGE_RE.search(line):
        return None
    m = _GATE_Q_START_RE.match(line)
    if m:
        return m.group(1)
    m = _PROBLEM_WORD_START_RE.match(line)
    if m:
        return m.group(1)
    m = _QUESTION_WORD_START_RE.match(line)
    if m and not _QUESTION_CONTINUED_RE.match(line):
        return m.group(1)
    # Numeric patterns are only accepted as question starts if the line also references points.
    # This avoids triggering on code listings with line numbers.
    # Important: do NOT match words like "Point" (e.g., Java `Point` class) as "points".
    has_points = bool(_POINTS_RE.search(line))
    if has_points:
        m = _NUM_PAREN_START_RE.match(line)
        if m:
            return m.group(1)
        m = _NUM_DOT_START_RE.match(line)
        if m:
            return m.group(1)
    m = _NUM_TITLE_START_RE.match(line)
    if m:
        title = m.group(2)
        if has_points and re.search(r"[A-Za-z]", title):
            return m.group(1)
    return None


_ANSWER_MARKERS = (
    re.compile(r"^\s*Solution\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^\s*Answer\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^\s*Ans\.?\s*:?\s*$", re.IGNORECASE),
)

_ANSWER_INLINE_RE = re.compile(
    r"^\s*(solution|answer|ans\.?|correct\s+answer|key)\s*(?:[:\-–])\s*(.*?)\s*$",
    re.IGNORECASE,
)

_POINTS_FOR_CORRECT_ANSWER_RE = re.compile(
    r"^\s*(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"points?\s+for\s+correct\s+answer\s*:?\s*(.*?)\s*$",
    re.IGNORECASE,
)

_PROMPT_CUE_RE = re.compile(
    r"(?i)\b(?:explain|justify|describe|discuss|list|compute|show|draw|prove|state|find|determine|give)\b"
)

_SOLUTION_GRADING_SPLIT_RE = re.compile(
    r"(?i)^\s*(?:we\s+(?:deducted|subtracted)\b|for\s+partially\s+correct\s+answers\b|-\d+\s+(?:pts?|points?)\b|-\d+\s+missing\b)"
)

_OPTION_LINE_RE = re.compile(r"^\s*(?:\(?[A-H]\)?[.)]|[A-H]\s*[.)]|[A-H]\s*[-–])\s+\S")


def _leading_indent_cols(s: str) -> int:
    cols = 0
    for ch in s:
        if ch == " ":
            cols += 1
        elif ch == "\t":
            cols += 4
        else:
            break
    return cols


def _looks_like_solution_file(txt: str, path: Path) -> bool:
    name = path.name.lower()
    if "solutions" in name:
        return True

    # Some exams include phrases like “Write your solutions …” which should not trigger
    # answer inference. Instead, require explicit standalone markers.
    marker_lines = 0
    for line in txt.splitlines()[:2000]:
        s = line.strip()
        if not s:
            continue
        if s.lower() in {"solution:", "solution", "answer:", "answer", "ans:", "ans"}:
            marker_lines += 1
            if marker_lines >= 2:
                return True
        if re.match(r"^\s*solutions\s+name\s*:\s*", s, flags=re.IGNORECASE):
            return True

    return False


def _looks_like_prompt_boundary(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if "?" in s:
        return True
    if s.endswith(":"):
        return True
    return bool(_PROMPT_CUE_RE.search(s))


def _looks_like_diagram_answer(lines: list[str]) -> bool:
    # Common in solution PDFs: the “answer” is a small diagram / graph / table rendered as text tokens.
    # We prefer high-signal cues to avoid splitting at multiple-choice option lists.
    joined = "\n".join(lines)
    if re.search(r"\bT\d+\b", joined):
        return True
    if any(tok in joined for tok in ("->", "<-", "→", "←", "⇒", "⟶")):
        return True
    return False


def _split_question_answer(block_lines: list[TextLine]) -> tuple[str, str | None]:
    # Heuristic: first "Answer:"/"Solution:" marker splits Q vs A.
    idx = None
    inline_tail: str | None = None

    for i, tl in enumerate(block_lines):
        m = _ANSWER_INLINE_RE.match(tl.text)
        if not m:
            continue
        idx = i
        inline_tail = (m.group(2) or "").strip()
        break

    for i, tl in enumerate(block_lines):
        for rx in _ANSWER_MARKERS:
            if rx.match(tl.text):
                idx = i
                inline_tail = None
                break
        if idx is not None:
            break
    # Common in “solutions” PDFs: "Why?" then the actual answer.
    if idx is None:
        for i, tl in enumerate(block_lines):
            if tl.text.strip().lower() == "why?":
                idx = i
                inline_tail = None
                break

    def join(lines: list[TextLine]) -> str:
        text = "\n".join(t.text.rstrip() for t in lines).strip("\n")
        return text

    if idx is None:
        return join(block_lines), None

    # "Why?" stays as part of the question prompt; answers start after it.
    if block_lines[idx].text.strip().lower() == "why?":
        q = join(block_lines[: idx + 1])
        a = join(block_lines[idx + 1 :])
        return q, (a if a else None)

    # Inline marker line is excluded from the question.
    q = join(block_lines[:idx])
    answer_lines: list[str] = []
    if inline_tail is not None and inline_tail != "":
        answer_lines.append(inline_tail)
    answer_lines.extend(t.text.rstrip() for t in block_lines[idx + 1 :])
    a = "\n".join(answer_lines).strip("\n")
    return q, (a if a else None)


def _split_question_answer_solution_fallback(segment_lines: list[TextLine]) -> tuple[str, str | None]:
    q_text, a_text = _split_question_answer(segment_lines)
    if a_text is not None:
        return q_text, a_text
    # If the source already contains an explicit answer marker but we couldn't extract any
    # answer text, don't try to infer an answer from formatting (common for diagram-only answers).
    for tl in segment_lines:
        if _ANSWER_INLINE_RE.match(tl.text):
            return q_text, None
        if tl.text.strip().lower() == "why?":
            return q_text, None
        for rx in _ANSWER_MARKERS:
            if rx.match(tl.text):
                return q_text, None

    def join(lines: list[TextLine]) -> str:
        return "\n".join(t.text.rstrip() for t in lines).strip("\n")

    # 1) "Three points for correct answer: ..." style markers.
    for i, tl in enumerate(segment_lines):
        m = _POINTS_FOR_CORRECT_ANSWER_RE.match(tl.text)
        if not m:
            continue
        inline = (m.group(1) or "").strip()
        q = join(segment_lines[:i])
        rest = [t.text.rstrip() for t in segment_lines[i + 1 :]]
        answer_lines = ([inline] if inline else []) + rest
        a = "\n".join(answer_lines).strip("\n")
        return q, (a if a else None)

    # 1.5) Grading/explanation split: many “solutions” PDFs include grader notes that clearly belong
    #      to the answer section and can serve as a reliable boundary (esp. for table/diagram answers).
    for i, tl in enumerate(segment_lines):
        if not _SOLUTION_GRADING_SPLIT_RE.match(tl.text):
            continue
        q = join(segment_lines[:i])
        a = join(segment_lines[i:])
        return q, (a if a else None)

    # 2) Blank-line boundary: common in solution PDFs (question prompt, blank line, then answer).
    prev_nonempty: str | None = None
    for i in range(len(segment_lines) - 1):
        if segment_lines[i].text.strip():
            prev_nonempty = segment_lines[i].text
            continue
        if prev_nonempty is None or not _looks_like_prompt_boundary(prev_nonempty):
            continue
        j = i + 1
        while j < len(segment_lines) and not segment_lines[j].text.strip():
            j += 1
        if j >= len(segment_lines):
            break
        window = [t.text.strip() for t in segment_lines[j : min(len(segment_lines), j + 10)] if t.text.strip()]
        if not window:
            continue
        cand = window[0]
        if re.match(r"^(?:\(?[a-z]\)?|[ivx]{1,6}\)|\d+[.)])\s+\S", cand, flags=re.IGNORECASE):
            continue
        if _OPTION_LINE_RE.match(cand):
            continue
        # Allow diagram/table answers that start with short tokens (e.g., a node label), but require
        # some signal that the following block is not just a list of options.
        content_window = [
            t for t in window if not re.match(r"^(?:\(?[a-z]\)?|[ivx]{1,6}\)|\d+[.)])\s+\S", t, flags=re.IGNORECASE)
        ]
        has_longish = any(len(t) >= 8 and re.search(r"[A-Za-z]", t) for t in content_window)
        if not (has_longish or _looks_like_diagram_answer(window)):
            continue
        q = join(segment_lines[:j])
        a = join(segment_lines[j:])
        return q, (a if a else None)

    # 3) Prompt cue then immediate prose: many solutions place the answer right after a question
    #    sentence (often ending with '?') without an explicit "Answer:" marker or a blank line.
    nonempty2 = [
        (i, _leading_indent_cols(tl.text), tl.text)
        for i, tl in enumerate(segment_lines)
        if tl.text.strip()
    ]
    if len(nonempty2) >= 3:
        prompt_seen = False
        for idx in range(len(nonempty2) - 1):
            i, indent_i, text_i = nonempty2[idx]
            prompt_seen = prompt_seen or _looks_like_prompt_boundary(text_i)

            j, indent_j, text_j = nonempty2[idx + 1]
            if not prompt_seen:
                continue
            cand = text_j.strip()
            if len(cand) < 12 or cand.endswith("?") or not re.search(r"[A-Za-z]", cand):
                continue
            if re.match(r"^(?:\(?[a-z]\)?|[ivx]{1,6}\)|\d+[.)])\s+\S", cand, flags=re.IGNORECASE):
                continue
            # Require a visible formatting transition between prompt and answer.
            if abs(indent_j - indent_i) < 2 and "?" not in text_i:
                continue
            # Require at least a couple of content lines after the split.
            tail_content = 0
            for _, _, t in nonempty2[idx + 1 : idx + 5]:
                if len(t.strip()) >= 8 and re.search(r"[A-Za-z]", t):
                    tail_content += 1
            if tail_content < 2:
                continue
            q = join(segment_lines[:j])
            a = join(segment_lines[j:])
            return q, (a if a else None)

    # 4) Indentation shift: question text often has smaller indent than the answer paragraphs.
    nonempty = [
        (i, _leading_indent_cols(tl.text), tl.text)
        for i, tl in enumerate(segment_lines)
        if tl.text.strip()
    ]
    if len(nonempty) >= 4:
        # Estimate the prompt indentation from the start of the segment only, otherwise
        # we risk "learning" the answer indentation when answers start early.
        sample = [indent for _, indent, _ in nonempty[: min(3, len(nonempty))]]
        sample_sorted = sorted(sample)
        q_indent = sample_sorted[len(sample_sorted) // 2]
        threshold = max(q_indent + 4, 6)
        for k, (i, indent, text) in enumerate(nonempty):
            if i == 0:
                continue
            if indent < threshold:
                continue
            s = text.strip()
            if len(s) < 12 or s.endswith("?"):
                continue
            follow = 0
            for _, ind2, t2 in nonempty[k : k + 4]:
                if ind2 >= threshold and len(t2.strip()) >= 8:
                    follow += 1
            if follow < 2:
                continue
            q = join(segment_lines[:i])
            a = join(segment_lines[i:])
            return q, (a if a else None)

    return q_text, None


# Subpart markers. Keep them strict to avoid matching e.g. "i)" as "(i)".
# - Letter: "(a) ..." or "a. ..."
# - Roman: "i) ...", "ii) ...", ...
_SUBPART_START_RE = re.compile(r"^\s*(?:\(([a-hj-uwyz])\)|([a-z])\.|([ivx]{1,6})\))\s+\S", re.IGNORECASE)


def _split_subparts(
    block_lines: list[TextLine], main_id: str | None, *, strict_letter_subparts: bool
) -> list[tuple[str | None, str | None, list[TextLine], list[TextLine]]]:
    # Split a question block into subparts like "(a) ..." / "a. ..." / "i) ...".
    #
    # Many exams use nested structure (e.g., "b." containing "i), ii), ..."). The older
    # logic flattened everything into `main_id.i`, which creates duplicate question_ids and
    # spurious "missing answers" on container headings. This function supports a simple
    # 2-level nesting:
    # - letter subparts: `main_id.a`, `main_id.b`, ...
    # - roman subparts nested under the most recent letter: `main_id.b.i`, `main_id.b.ii`, ...
    starts: list[tuple[int, str, str]] = []  # (index, token, kind)
    for i, tl in enumerate(block_lines):
        m = _SUBPART_START_RE.match(tl.text)
        if not m:
            continue
        if m.group(3):
            starts.append((i, m.group(3), "roman"))
            continue
        sub = m.group(1) or m.group(2)
        if sub:
            stripped = tl.text.lstrip()
            marker_char = stripped[1:2] if stripped.startswith("(") else stripped[:1]
            # Avoid splitting multiple-choice options like "A. ..." / "(A) ...".
            if marker_char.isupper() and marker_char in {"A", "B", "C", "D", "E"} and not _POINTS_RE.search(tl.text):
                continue
            # Some sources (notably question papers) use "(a)/(b)/(c)/(d)" for answer choices.
            # In strict mode, only treat letter markers as subparts when they carry an explicit
            # point value.
            if strict_letter_subparts and not _POINTS_RE.search(tl.text):
                continue
            starts.append((i, sub, "letter"))

    if len(starts) < 2:
        return [(main_id, None, [], block_lines)]

    def join_id(*parts: str | None) -> str | None:
        xs = [p for p in parts if p]
        return ".".join(xs) if xs else None

    starts.sort(key=lambda t: t[0])
    global_preamble = block_lines[: starts[0][0]]
    out: list[tuple[str | None, str | None, list[TextLine], list[TextLine]]] = []

    letter_starts = [(i, tok) for i, tok, kind in starts if kind == "letter"]
    roman_starts = [(i, tok) for i, tok, kind in starts if kind == "roman"]

    if letter_starts:
        # Process each letter section separately; attach the letter section preamble to its roman leaves.
        letter_starts_sorted = sorted(letter_starts, key=lambda t: t[0])
        for li, (letter_i, letter_tok) in enumerate(letter_starts_sorted):
            section_end = letter_starts_sorted[li + 1][0] if li + 1 < len(letter_starts_sorted) else len(block_lines)
            section_lines = block_lines[letter_i:section_end]

            section_roman = [(i - letter_i, tok) for i, tok in roman_starts if letter_i < i < section_end]
            letter_id = join_id(main_id, letter_tok)

            if section_roman:
                section_roman.sort(key=lambda t: t[0])
                letter_preamble = global_preamble + section_lines[: section_roman[0][0]]
                for ri, (rel_i, roman_tok) in enumerate(section_roman):
                    rel_end = section_roman[ri + 1][0] if ri + 1 < len(section_roman) else len(section_lines)
                    roman_id = join_id(letter_id, roman_tok)
                    out.append((roman_id, letter_id, letter_preamble, section_lines[rel_i:rel_end]))
            else:
                # Leaf letter part.
                out.append((letter_id, main_id, global_preamble, section_lines))
        return out

    # No letter sections: fall back to flat roman splitting.
    if len(roman_starts) >= 2:
        roman_starts_sorted = sorted(roman_starts, key=lambda t: t[0])
        for ri, (start_i, roman_tok) in enumerate(roman_starts_sorted):
            end_i = roman_starts_sorted[ri + 1][0] if ri + 1 < len(roman_starts_sorted) else len(block_lines)
            roman_id = join_id(main_id, roman_tok)
            out.append((roman_id, main_id, global_preamble, block_lines[start_i:end_i]))
        return out

    # Mixed/degenerate case: keep as a single block.
    return [(main_id, None, [], block_lines)]


def _extract_question_blocks(
    lines: list[TextLine], infer_answers: bool, *, strict_letter_subparts: bool
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    cur: list[TextLine] = []
    cur_id: str | None = None

    def flush() -> None:
        nonlocal cur, cur_id
        if not cur:
            cur_id = None
            return
        for qid, parent_qid, preamble, segment in _split_subparts(
            cur, cur_id, strict_letter_subparts=strict_letter_subparts
        ):
            if infer_answers:
                seg_q, seg_a = _split_question_answer_solution_fallback(segment)
            else:
                seg_q, seg_a = _split_question_answer(segment)

            preamble_text = "\n".join(t.text.rstrip() for t in preamble).strip("\n")
            q_text = "\n".join([t for t in [preamble_text, seg_q.strip("\n")] if t]).strip("\n")
            a_text = seg_a
            raw_lines = preamble + segment
            blocks.append(
                {
                    "question_id": qid,
                    "parent_question_id": parent_qid,
                    "page_start": raw_lines[0].page,
                    "page_end": raw_lines[-1].page,
                    "line_start": raw_lines[0].line_no,
                    "line_end": raw_lines[-1].line_no,
                    "raw_text": "\n".join(t.text for t in raw_lines).strip("\n") or None,
                    "question_text": q_text or None,
                    "answer_text": a_text,
                }
            )
        cur = []
        cur_id = None

    for tl in lines:
        cont = _QUESTION_CONTINUED_RE.match(tl.text)
        if cont and cur_id == cont.group(1) and cur:
            cur.append(tl)
            continue
        qid = _question_start_id(tl.text)
        if qid is not None:
            # Ignore repeated headers that look like question ids but have no body.
            if cur:
                flush()
            cur_id = qid
            cur.append(tl)
            continue
        if cur:
            cur.append(tl)

    flush()
    # Filter out tiny/empty blocks (common from stray numeric headings)
    filtered: list[dict[str, Any]] = []
    for b in blocks:
        qt = (b.get("question_text") or "").strip()
        if len(qt) < 10:
            continue
        filtered.append(b)
    return filtered


_GATE_KEY_ROW_RE = re.compile(r"^\s*(\d{1,3})\s+(\d)\s+(MCQ|MSQ|NAT)\s+([A-Z]{2}(?:-\d+)?)\s+(.+?)\s+(\d)\s*$")


def _extract_gate_answer_key(lines: list[TextLine]) -> list[dict[str, Any]] | None:
    # Detect: lots of rows like "1 1 MCQ GA A 1"
    # We'll parse with a flexible splitter to tolerate multi-token "Key/Range" fields.
    candidate_rows: list[tuple[TextLine, dict[str, Any]]] = []
    for tl in lines:
        s = tl.text.strip()
        if not s:
            continue
        # Skip the header row.
        if "Q. No." in s and "Key/Range" in s:
            continue
        parts = s.split()
        if len(parts) < 6:
            continue
        if not parts[0].isdigit() or not parts[1].isdigit():
            continue
        qno = parts[0]
        session = parts[1]
        qtype = parts[2]
        section = parts[3]
        marks = parts[-1]
        if qtype not in {"MCQ", "MSQ", "NAT"}:
            continue
        if not marks.isdigit():
            continue
        key_range = " ".join(parts[4:-1]).strip()
        if not key_range:
            continue
        # Basic sanity: key/range for MCQ/MSQ should contain letters; NAT should contain digits or '-' or 'to'
        if qtype in {"MCQ", "MSQ"} and not re.search(r"[A-D]", key_range):
            continue
        if qtype == "NAT" and not re.search(r"[\d\-]", key_range):
            continue
        candidate_rows.append(
            (
                tl,
                {
                    "question_id": qno,
                    "gate_session": session,
                    "gate_qtype": qtype,
                    "gate_section": section,
                    "answer_text": key_range,
                    "gate_marks": marks,
                    "page_start": tl.page,
                    "page_end": tl.page,
                    "line_start": tl.line_no,
                    "line_end": tl.line_no,
                    "raw_text": tl.text,
                    "question_text": None,
                },
            )
        )

    if len(candidate_rows) < 10:
        return None

    # Preserve original order in file.
    return [row for _, row in candidate_rows]


def _looks_like_gate_answer_key_file(txt: str, path: Path) -> bool:
    name = path.name.lower()
    if "answer key" in name:
        return True
    if "answer key" in txt[:2000].lower():
        return True
    return False


def extract_qa_records_from_txt(txt_path: Path) -> list[dict[str, Any]]:
    txt = txt_path.read_text(encoding="utf-8", errors="strict")
    header = _extract_header_fields(txt)
    lines = _build_line_stream(txt)
    infer_answers = _looks_like_solution_file(txt, txt_path)
    strict_letter_subparts = ("gate" in txt_path.name.lower()) and ("question paper" in txt_path.name.lower())

    records: list[dict[str, Any]]
    if _looks_like_gate_answer_key_file(txt, txt_path):
        gate = _extract_gate_answer_key(lines)
        if gate is not None:
            records = gate
            for r in records:
                r["parser"] = "gate_answer_key_v1"
        else:
            records = _extract_question_blocks(
                lines,
                infer_answers=infer_answers,
                strict_letter_subparts=strict_letter_subparts,
            )
            for r in records:
                r["parser"] = "question_blocks_v1"
    else:
        records = _extract_question_blocks(
            lines,
            infer_answers=infer_answers,
            strict_letter_subparts=strict_letter_subparts,
        )
        for r in records:
            r["parser"] = "question_blocks_v1"

    # Add provenance + order.
    for i, r in enumerate(records, start=1):
        r["order_in_source"] = i
        r["source_txt"] = str(txt_path.resolve())
        for k, v in header.items():
            r[k] = v
    return records


_LEAK_RE = re.compile(r"(?im)^\s*(answer|solution|ans\.?|correct\s+answer|key)\s*(?::|[-–])")


def _verify_records(records: list[dict[str, Any]]) -> dict[str, int]:
    leaked_in_question = 0
    missing_answer_but_marker_in_raw = 0
    for r in records:
        qt = r.get("question_text") or ""
        rt = r.get("raw_text") or ""
        at = r.get("answer_text")
        if qt and _LEAK_RE.search(qt):
            leaked_in_question += 1
        if at is None and rt and _LEAK_RE.search(rt):
            missing_answer_but_marker_in_raw += 1
    return {
        "leaked_markers_in_question_text": leaked_in_question,
        "missing_answer_text_but_marker_in_raw_text": missing_answer_but_marker_in_raw,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract questions/answers from extracted PDF .txt files.")
    p.add_argument("--in-dir", default="extracted_txt", help="Directory containing extracted .txt files.")
    p.add_argument("--out-dir", default="qa_extracted", help="Output directory.")
    p.add_argument("--overwrite", action="store_true", help="Overwrite output files if present.")
    p.add_argument(
        "--per-source",
        action="store_true",
        help="Also write one JSONL per source file under <out-dir>/by_source/.",
    )
    p.add_argument(
        "--merge-gate",
        choices=["auto", "off", "strict"],
        default="auto",
        help="Auto-merge detected GATE Question Paper + Answer Key into <out-dir>/gate_qa_merged.jsonl and <out-dir>/qa_records_merged.jsonl.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    in_dir = Path(args.in_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not in_dir.exists():
        _eprint(f"Input dir not found: {in_dir}")
        return 2

    txts = _iter_txts(in_dir)
    if not txts:
        _eprint(f"No .txt files found in: {in_dir}")
        return 2

    out_jsonl = out_dir / "qa_records.jsonl"
    out_jsonl_merged = out_dir / "qa_records_merged.jsonl"
    out_summary = out_dir / "qa_summary.json"
    out_gate = out_dir / "gate_qa_merged.jsonl"
    if not args.overwrite and (out_jsonl.exists() or out_jsonl_merged.exists() or out_gate.exists() or out_summary.exists()):
        _eprint(f"Refusing to overwrite existing outputs in {out_dir}. Use --overwrite.")
        return 2

    all_records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"sources": [], "total_records": 0}

    by_source_dir: Path | None = out_dir / "by_source" if args.per_source else None
    if by_source_dir is not None:
        by_source_dir.mkdir(parents=True, exist_ok=True)

    for txt_path in txts:
        recs = extract_qa_records_from_txt(txt_path)
        verification = _verify_records(recs)
        all_records.extend(recs)
        summary["sources"].append(
            {
                "source_txt": str(txt_path.resolve()),
                "records": len(recs),
                "parsers": sorted({r.get("parser") for r in recs}),
                "verification": verification,
            }
        )
        msg = f"[OK] {txt_path.name}: {len(recs)} record(s)"
        if verification["leaked_markers_in_question_text"] or verification["missing_answer_text_but_marker_in_raw_text"]:
            msg += f" (verify={verification})"
        _eprint(msg)

        if by_source_dir is not None:
            stem = txt_path.stem
            out_src = by_source_dir / f"{stem}.jsonl"
            content = "".join(_jsonl_dumps(r) + "\n" for r in recs)
            _atomic_write_text(out_src, content)

    summary["total_records"] = len(all_records)
    _atomic_write_text(out_jsonl, "".join(_jsonl_dumps(r) + "\n" for r in all_records))
    _eprint(f"Wrote: {out_jsonl}")

    # Optional: auto-merge GATE question paper + answer key into a unified QA view.
    merged_records: list[dict[str, Any]] | None = None
    if args.merge_gate != "off":
        try:
            import gate_qa_merge

            gate_merged, used_pdfs = gate_qa_merge.merge_gate_pairs_from_records(all_records)
            if gate_merged:
                merged_records = [r for r in all_records if (r.get("source_pdf") or "") not in used_pdfs] + gate_merged
                out_gate = out_dir / "gate_qa_merged.jsonl"
                _atomic_write_text(out_gate, "".join(_jsonl_dumps(r) + "\n" for r in gate_merged))
                _atomic_write_text(out_jsonl_merged, "".join(_jsonl_dumps(r) + "\n" for r in merged_records))
                summary["gate_merge"] = {
                    "merged_records": len(gate_merged),
                    "replaced_source_pdfs": sorted(used_pdfs),
                    "output_gate_qa_merged_jsonl": str(out_gate),
                    "output_qa_records_merged_jsonl": str(out_jsonl_merged),
                }
                summary["total_records_merged"] = len(merged_records)
                _eprint(f"Wrote: {out_gate}")
                _eprint(f"Wrote: {out_jsonl_merged}")
        except Exception as e:
            if args.merge_gate == "strict":
                raise
            _eprint(f"[WARN] GATE merge skipped: {e}")

    _atomic_write_text(out_summary, _json_dumps(summary) + "\n")
    _eprint(f"Wrote: {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
