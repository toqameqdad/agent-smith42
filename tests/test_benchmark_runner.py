import json
from pathlib import Path

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


def test_benchmark_run_writes_required_evaluation_log_files(tmp_path, monkeypatch):
    from benchmark_runner import _run_agent

    task_file = tmp_path / "task.json"
    task_file.write_text('{"instance_id":"repo__issue-9"}', encoding="utf-8")
    task = {
        "resolved_task_file": str(task_file),
        "resolved_task_id": "repo__issue-9",
    }
    output_path = tmp_path / "results" / "solution.json"
    evaluation_dir = tmp_path / "evaluations" / "SWEBENCH" / "2026-11-02_12-00-00" / "repo__issue-9"

    class FakeProcess:
        returncode = 0

        def __init__(self, command, cwd, stdout, stderr, text):
            self.command = command
            self.stdout_file = stdout
            self.stderr_file = stderr

        def wait(self, timeout=None):
            self.stdout_file.write("agent stdout\n")
            self.stderr_file.write("agent stderr\n")
            output_index = self.command.index("--output") + 1
            output = Path(self.command[output_index])
            output.write_text(json.dumps({
                "task_id": "repo__issue-9",
                "benchmark": "swebench",
                "success": True,
                "solution": "diff",
                "iterations": 1,
                "total_requests": 1,
                "total_input_tokens": 10,
                "total_output_tokens": 5,
                "total_time_seconds": 1.0,
                "steps": [],
                "system_prompt": "prompt",
                "timestamp": "2026-11-02T12:00:00",
            }), encoding="utf-8")

    monkeypatch.setattr("benchmark_runner.subprocess.Popen", FakeProcess)
    record = _run_agent(
        {
            "name": "provider",
            "provider_url": "https://provider.example/v1",
            "api_key_env": ["KEY"],
        },
        "model",
        task,
        output_path,
        evaluation_dir,
        "standard",
        30,
    )

    assert record["result"]["success"] is True
    assert (evaluation_dir / "task.json").is_file()
    assert (evaluation_dir / "solution.json").is_file()
    assert (evaluation_dir / "stdout.log").read_text() == "agent stdout\n"
    assert (evaluation_dir / "stderr.log").read_text() == "agent stderr\n"
