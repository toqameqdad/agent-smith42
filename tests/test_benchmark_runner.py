import json

from benchmark_runner import (
    _first_passing_test_step,
    _first_relevant_file_step,
    _load_config,
    _render_report,
    ROOT,
)


def test_benchmark_config_requires_two_providers_five_models_and_three_tasks(tmp_path):
    task_files = []
    for index in range(3):
        task_file = tmp_path / f"task-{index}.json"
        task_file.write_text(
            json.dumps({
                "instance_id": f"repo__issue-{index}",
                "problem_statement": "Fix the issue.",
                "docker_image": "example/image",
                "eval_script": "pytest -q",
            }),
            encoding="utf-8",
        )
        task_files.append(task_file)

    config = {
        "providers": [
            {
                "name": "provider-a",
                "provider_url": "https://a.example/v1",
                "api_key_env": ["KEY_A"],
                "models": ["model-a1", "model-a2", "model-a3"],
            },
            {
                "name": "provider-b",
                "provider_url": "https://b.example/v1",
                "api_key_env": ["KEY_B"],
                "models": ["model-b1", "model-b2"],
            },
        ],
        "tasks": [
            {
                "task_file": str(task_file),
                "rationale": "Selected for repository size and debugging complexity.",
            }
            for task_file in task_files
        ],
        "ablation": {
            "provider_name": "provider-a",
            "model_name": "model-a1",
            "prompt_variant": "compact",
        },
    }
    config_file = tmp_path / "benchmark.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    loaded = _load_config(config_file)
    assert len(loaded["providers"]) == 2
    assert len(loaded["tasks"]) == 3
    assert loaded["tasks"][0]["resolved_task_id"] == "repo__issue-0"


def test_benchmark_intermediary_metrics_are_extracted_from_trace():
    result = {
        "solution": "--- a/src/fix.py\n+++ b/src/fix.py\n@@ -1 +1 @@\n",
        "iterations": 5,
        "steps": [
            {
                "step": 1,
                "sandbox_input": "result = read_file(filepath='/testbed/src/fix.py')",
                "sandbox_output": "file contents",
            },
            {
                "step": 3,
                "sandbox_input": "result = run_tests()",
                "sandbox_output": "{'stdout': '2 passed', 'exit_code': 0}",
            },
        ],
    }
    assert _first_relevant_file_step(result) == "1"
    assert _first_passing_test_step(result) == 3


def test_benchmark_report_contains_results_reliability_and_ablation():
    task = {
        "resolved_task_id": "repo__issue-1",
        "resolved_task_file": "/tasks/issue.json",
        "rationale": "Small task with a focused regression test.",
    }
    provider = {
        "name": "provider-a",
        "provider_url": "https://a.example/v1",
        "models": ["model-a"],
    }
    result = {
        "success": True,
        "iterations": 3,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "total_requests": 3,
        "steps": [
            {"step": 1, "request_time_ms": 20, "retries": 0, "sandbox_input": "", "sandbox_output": ""},
            {"step": 2, "request_time_ms": 25, "retries": 1, "sandbox_input": "run_tests()", "sandbox_output": "{'exit_code': 0}"},
        ],
        "solution": "--- a/a.py\n+++ b/a.py\n",
    }
    baseline = {
        "provider": "provider-a",
        "model": "model-a",
        "task_id": "repo__issue-1",
        "variant": "standard",
        "evaluation_dir": str(ROOT / "evaluations" / "SWEBENCH" / "2026-01-01_00-00-00" / "repo__issue-1"),
        "result": result,
        "wall_seconds": 12.0,
    }
    ablation = {**baseline, "variant": "compact"}
    report = _render_report(
        [baseline, ablation],
        {
            "providers": [provider],
            "tasks": [task],
        },
    )
    assert "## 2. Results" in report
    assert "## 3. Provider reliability" in report
    assert "## 5. Ablation study" in report
    assert "standard vs compact" in report
