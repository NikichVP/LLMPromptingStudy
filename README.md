# PROMPT-RESEARCH

**Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions**

A controlled study of how system-prompt design changes the performance and response behavior of the same language model on a fixed set of 101 exam-style Computer Science questions.

## Study design

- **101 questions:** 49 objective-format and 52 open-ended
- **Model held fixed:** GPT-4o
- **Main comparison:** baseline, Reasoning chain v1, and Multiple solutions v2
- **Additional iterations:** Reasoning chain v2 and Multiple solutions v1 are reported in the paper appendix
- **Objective metric:** strict and lenient answer matching
- **Open-ended metric:** unigram F1 against a reference answer, used as an approximate comparison signal rather than a complete correctness metric

## Results

| Prompt variant | Objective accuracy (lenient) | Mean open-ended F1 | Avg. words |
|---|---:|---:|---:|
| Baseline | 34/49 (69.4%) | 0.195 | 255.6 |
| Reasoning chain v1 | **38/49 (77.6%)** | 0.223 | 130.9 |
| Reasoning chain v2 | 35/49 (71.4%) | **0.254** | 131.7 |
| Multiple solutions v1 | 30/49 (61.2%) | 0.249 | **62.9** |
| Multiple solutions v2 | 36/49 (73.5%) | 0.245 | 135.0 |

Within the three main conditions, **Reasoning chain v1** produced the best objective accuracy, while **Multiple solutions v2** produced the strongest open-ended overlap. Across all five iterative variants, the results show a trade-off between objective accuracy, open-ended overlap, verbosity, and output discipline rather than one prompt dominating every metric.

## Repository contents

### Core experiment

- `prompts.py` — prompt definitions and refined variants
- `queries_to_gpt.py` — GPT-4o experiment runner
- `questions_for_usage.json` — curated 101-question experiment input
- `gpt_answers_4o_without_prompt.json` — baseline outputs
- `gpt_answers_4o_with_reasoning_chain.json` — Reasoning chain v1 outputs
- `gpt_answers_4o_reasoning2.json` — Reasoning chain v2 outputs
- `gpt_answers_4o_multiple_solutions.json` — Multiple solutions v1 outputs
- `gpt_answers_4o_solutions2.json` — Multiple solutions v2 outputs

### Dataset preparation utilities

- `extractor.py` — PDF/text extraction utility
- `qa_extractor.py` — question/answer extraction pipeline
- `gate_qa_merge.py` — GATE question/answer merge utility
- `export_clean_qa.py` — cleaned dataset export
- `gpt_questions_checker.py` — question-quality screening utility

### Paper

- `PROMPT_RESEARCH.pdf` — final research paper

## Running an experiment

The runner reads `questions_for_usage.json` and uses an API key from the environment:

```bash
export OPENAI_API_KEY="..."
python queries_to_gpt.py
```

To reproduce a particular condition, set the corresponding system prompt and output filename in `queries_to_gpt.py` before running it.

## Data note

The original third-party exam PDFs and large intermediate extraction artifacts used during dataset construction are intentionally not kept in the current working tree. The repository keeps the experiment code, curated input, saved model outputs, and the authored paper.

## Paper

**Nikita Zhdanovich. _PROMPT-RESEARCH: Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions._ 2026.**
