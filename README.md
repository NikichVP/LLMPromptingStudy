# PROMPT-RESEARCH

**Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions**

A controlled study of how system-prompt design changes the performance and response behavior of the same model on a fixed set of 101 exam-style Computer Science questions.

Presented at the **Letovo School Research Conference 2026**.

[Final paper (PDF)](./PROMPT_RESEARCH.pdf)

## Experiment

The study used 101 questions: 49 objective-format and 52 open-ended. GPT-4o and the user questions were held fixed while the system prompt changed.

Five prompt conditions were tested across the development process:

- baseline
- reasoning chain v1
- reasoning chain v2
- multiple solutions v1
- multiple solutions v2

The paper treats baseline, reasoning chain v1, and multiple solutions v2 as the main comparison; the other two variants are reported as refinements.

## Results

| Prompt variant | Objective accuracy (lenient) | Mean open-ended F1 | Avg. words |
|---|---:|---:|---:|
| Baseline | 34/49 (69.4%) | 0.195 | 255.6 |
| Reasoning chain v1 | **38/49 (77.6%)** | 0.223 | 130.9 |
| Reasoning chain v2 | 35/49 (71.4%) | **0.254** | 131.7 |
| Multiple solutions v1 | 30/49 (61.2%) | 0.249 | **62.9** |
| Multiple solutions v2 | 36/49 (73.5%) | 0.245 | 135.0 |

The strongest objective result was reasoning chain v1: **77.6% vs. 69.4% for baseline (+8.2 percentage points)**. The variants also showed a trade-off between answer accuracy, open-ended overlap, verbosity, and output discipline.

## Repository

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

The saved result files contain the model outputs used in the study.

Third-party exam PDFs, answer keys, extracted source text, and the question dataset itself are intentionally not redistributed in this repository. Their provenance and the dataset construction process are described in the paper.

## Reproducing a run

The runner accepts a JSON file containing objects with `id` and `question` fields.

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

- one model family in the main experiment
- one run per condition, so sampling variance was not estimated
- 101 questions: useful for a focused comparison, not a universal benchmark
- unigram F1 is an approximate signal for open-ended answers and can penalize valid paraphrases
- objective scoring depends partly on answer extraction and formatting

## Citation

Nikita Zhdanovich. *PROMPT-RESEARCH: Evaluating Prompting Strategies for GPT-4o on Exam-Style Computer Science Questions.* 2026.
