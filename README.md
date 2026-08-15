# PROMPT-RESEARCH

**Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions**

A controlled study of how system-prompt design changes the performance and response behavior of the same model on a fixed set of 101 exam-style Computer Science questions.

**[Read the final paper](./PROMPT_RESEARCH.pdf)**

Presented at the **Letovo School Research Conference 2026**.

## Study design

The dataset contains 101 questions: 49 objective-format items and 52 open-ended items. GPT-4o and the user questions were held fixed while only the system prompt changed.

The paper's primary comparison uses three conditions:

- baseline
- reasoning chain v1
- multiple solutions v2

Two additional variants from iterative development — reasoning chain v2 and multiple solutions v1 — are reported in the appendix and preserved here for reproducibility.

## Results

| Prompt variant | Objective accuracy (lenient) | Mean open-ended F1 | Avg. words |
|---|---:|---:|---:|
| Baseline | 34/49 (69.4%) | 0.195 | 255.6 |
| Reasoning chain v1 | **38/49 (77.6%)** | 0.223 | 130.9 |
| Reasoning chain v2 | 35/49 (71.4%) | **0.254** | 131.7 |
| Multiple solutions v1 | 30/49 (61.2%) | 0.249 | **62.9** |
| Multiple solutions v2 | 36/49 (73.5%) | 0.245 | 135.0 |

Among the primary conditions, reasoning chain v1 produced the strongest objective result: **77.6% vs. 69.4% for baseline (+8.2 percentage points)**. Multiple solutions v2 produced the strongest open-ended overlap among those same primary conditions. Across all five evaluated variants, the results show a trade-off between objective accuracy, open-ended overlap, verbosity, and output discipline.

## Repository structure

```text
experiment/
  prompts.py          exact system-prompt variants
  run_experiment.py   experiment runner
results/
  baseline.json
  reasoning_chain_v1.json
  reasoning_chain_v2.json
  multiple_solutions_v1.json
  multiple_solutions_v2.json
PROMPT_RESEARCH.pdf   final paper
```

The saved JSON files contain the model outputs used in the study. Third-party exam PDFs, answer keys, extracted source text, and the question dataset itself are intentionally not redistributed; their provenance and construction are described in the paper.

## Reproducing a run

The runner accepts a JSON array containing objects with `id` and `question` fields.

```json
[
  {"id": 1, "question": "..."}
]
```

Set an API key in the environment and run one condition:

```bash
export OPENAI_API_KEY="..."
python experiment/run_experiment.py \
  --condition reasoning_chain_v1 \
  --input /path/to/questions.json \
  --output /path/to/output.json
```

The default model is `gpt-4o`, matching the study.

## Limitations

- single model and one run per condition; sampling variance was not estimated
- 101 questions form a focused probe rather than a universal benchmark
- unigram F1 is only an approximate signal for open-ended answer quality
- objective scoring depends partly on answer extraction and formatting

## Citation

Nikita Zhdanovich. *PROMPT-RESEARCH: Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions.* 2026.
