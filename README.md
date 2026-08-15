# Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions

A controlled study of how system prompts change the performance and response behavior of the same language model on a fixed set of **101 exam-style computer science questions**.

The main experiment compares three conditions while keeping the model and user questions fixed:

1. **Baseline** — a minimal helpful-assistant system prompt
2. **Reasoning chain** — structured decomposition, verification, edge-case checks, and format discipline
3. **Multiple solutions** — adaptive independent verification and consistency checks

## Main result

On the 49 objective-format questions, the reasoning-chain condition reached **77.6% lenient accuracy (38/49)** versus **69.4% (34/49)** for the baseline, an improvement of **8.2 percentage points**.

The study also tracks strict-format accuracy, open-ended unigram-F1 overlap, verbosity, and recurring failure modes. The paper treats the open-ended metric as an approximate comparison signal rather than a complete measure of correctness.

## Dataset

The evaluation set contains:

- **49 objective questions** — single-choice, multi-select, numeric-range, and fraction answers
- **52 open-ended questions** — short explanations, definitions, and justifications

The dataset is a targeted probe of prompt sensitivity on exam-like CS questions, not a universal benchmark.

## Repository structure

- `prompts.py` — system-prompt variants
- `queries_to_gpt.py` — model-query runner
- `gpt_questions_checker.py` — question-quality screening utility
- `gpt_answers_*.json` — saved model outputs from experimental runs
- `questions_for_usage.json` — experiment input set
- `qa_extracted/` — intermediate extraction artifacts
- `extractor.py`, `qa_extractor.py`, `export_clean_qa.py`, `gate_qa_merge.py` — dataset preparation pipeline

## Limitations

- one model family in the main experiment
- one run per condition, so sampling variance is not estimated
- 101 questions, which is sufficient for a focused comparison but not broad benchmark claims
- open-ended evaluation relies on unigram F1 and may penalize valid paraphrases
- objective scoring depends partly on answer extraction and formatting

## Paper

**PROMPT-RESEARCH: Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions**  
Nikita Zhdanovich, 2026

Presented at the **Letovo School Research Conference 2026**.

> Public-release note: this working repository contains raw and intermediate source material used during dataset construction. A separate curated public release should include only redistributable inputs, experiment code, results, and the paper; third-party exam PDFs should not be republished without permission.
