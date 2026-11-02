# Agent Smith Model Benchmark Report

**Status: Pending execution**

This report is intentionally a template until the benchmark runs have been completed. No
model scores, token counts, timings, or provider reliability figures are fabricated.

## 1. Setup

- **Providers:** pending (minimum 2)
- **Models:** pending (minimum 5 across the selected providers)
- **SWE-bench tasks:** pending (minimum 3 identical tasks for every model)
- **Selection rationale:** choose tasks with different repository sizes and debugging
  characteristics. Use the same task set, container images, evaluation scripts, and agent
  limits for every model.

## 2. Results

Complete one row for every model × task combination.

| Provider | Model | Task ID | Pass/Fail | Iterations | Input tokens | Output tokens | Wall-clock time (s) |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Pending | Pending | Pending | Pending | — | — | — | — |

## 3. Provider reliability

| Provider | Model | Avg. response time/request (ms) | Retries | Availability |
| --- | --- | ---: | ---: | --- |
| Pending | Pending | — | — | — |

## 4. Intermediary metrics

At least two intermediary metrics are required by the subject. Record them from the generated
solution.json traces.

| Provider | Model | Task ID | First relevant file read/edited (step) | First test improvement (step) | Iterations after first passing tests |
| --- | --- | --- | ---: | ---: | ---: |
| Pending | Pending | Pending | — | — | — |

## 5. Analysis

To be completed after all runs. Compare solve rate, exploration efficiency, token usage,
iteration discipline, response latency, retry behavior, and provider availability. Explain
where model behavior differed and whether the same agent prompt and tool setup worked
consistently across providers.

## 6. Reproducibility

Record the repository commit, provider endpoint, model identifiers, task IDs, timestamps,
environment limits, and the corresponding evaluation output paths for every run.
