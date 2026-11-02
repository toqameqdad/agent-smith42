"""Run a reproducible SWE-bench model matrix and generate BENCHMARK_REPORT.md."""

import argparse
import json
from datetime import datetime, timezone
import re
import signal
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unnamed"


def _task_id(task_file: Path) -> str:
    data = json.loads(task_file.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("task"), dict):
        data = data["task"]
    return str(data.get("instance_id") or data.get("task_id") or task_file.stem)


def _load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    providers = config.get("providers", [])
    tasks = config.get("tasks", [])
    model_count = sum(len(provider.get("models", [])) for provider in providers)

    if len(providers) < 2:
        raise ValueError("Benchmark config must define at least two providers.")
    if model_count < 5:
        raise ValueError("Benchmark config must define at least five models in total.")
    if len(tasks) < 3:
        raise ValueError("Benchmark config must define at least three SWE-bench tasks.")

    names = [provider.get("name") for provider in providers]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("Provider names must be non-empty and unique.")

    config_dir = path.resolve().parent
    for task in tasks:
        task_path = Path(task["task_file"])
        if not task_path.is_absolute():
            task_path = config_dir / task_path
        if not task_path.is_file():
            raise FileNotFoundError(task_path)
        task["resolved_task_file"] = str(task_path.resolve())
        task["resolved_task_id"] = str(task.get("task_id") or _task_id(task_path))
        if not task.get("rationale"):
            raise ValueError(
                f"Add a selection rationale for task {task['resolved_task_id']}."
            )

    ablation = config.get("ablation")
    if not isinstance(ablation, dict):
        raise ValueError(
            "Add an ablation object selecting provider_name, model_name, "
            "and prompt_variant (for example compact)."
        )
    if ablation.get("prompt_variant") not in {"standard", "compact"}:
        raise ValueError("Ablation prompt_variant must be standard or compact.")
    selected_provider = next(
        (item for item in providers if item["name"] == ablation.get("provider_name")),
        None,
    )
    if not selected_provider or ablation.get("model_name") not in selected_provider["models"]:
        raise ValueError("Ablation provider/model must exist in the providers list.")

    return config


def _run_agent(
    provider: dict[str, Any],
    model_name: str,
    task: dict[str, Any],
    output_path: Path,
    prompt_variant: str,
    max_iterations: int,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    api_key_env = provider.get("api_key_env", [])
    if isinstance(api_key_env, str):
        api_key_env = [api_key_env]
    command = [
        sys.executable,
        str(ROOT / "agent_swebench.py"),
        "--task-file", task["resolved_task_file"],
        "--output", str(output_path),
        "--model-name", model_name,
        "--provider-url", provider["provider_url"],
        "--provider-name", provider["name"],
        "--api-key-env", ",".join(api_key_env),
        "--max-iterations", str(max_iterations),
        "--prompt-variant", prompt_variant,
    ]

    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr = ""
    try:
        _, stderr = process.communicate(timeout=1200)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGTERM)
        try:
            _, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
    elapsed = time.monotonic() - started

    if output_path.is_file():
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = None
    else:
        result = None

    if result is None:
        result = {
            "task_id": task["resolved_task_id"],
            "benchmark": "swebench",
            "success": False,
            "solution": "",
            "iterations": 0,
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_time_seconds": elapsed,
            "steps": [],
            "system_prompt": "",
            "error": (
                f"Agent process exited with code {process.returncode}. "
                f"{(stderr or '').strip()[-3000:]}"
            ),
        }
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    return {
        "provider": provider["name"],
        "model": model_name,
        "task_id": task["resolved_task_id"],
        "variant": prompt_variant,
        "output_path": str(output_path),
        "result": result,
        "wall_seconds": elapsed,
        "return_code": process.returncode,
        "stderr": stderr or "",
    }


def _patch_files(patch: str) -> list[str]:
    return sorted(set(re.findall(r"^\+\+\+ b/(.+)$", patch, flags=re.MULTILINE)))


def _first_relevant_file_step(result: dict[str, Any]) -> str:
    files = _patch_files(result.get("solution", ""))
    if not files:
        return "N/A"
    for step in result.get("steps", []):
        code = step.get("sandbox_input", "")
        if not re.search(r"\b(read_file|edit_file)\s*\(", code):
            continue
        if any(path in code or Path(path).name in code for path in files):
            return str(step.get("step", "?"))
    return "Not observed"


def _first_passing_test_step(result: dict[str, Any]) -> int | None:
    for step in result.get("steps", []):
        code = step.get("sandbox_input", "")
        output = step.get("sandbox_output", "").lower()
        is_test_action = (
            "run_tests" in code
            or "pytest" in code
            or "unittest" in code
        )
        passed = bool(
            re.search(r"exit_code['\"]?\s*:\s*0", output)
            or re.search(r"\b\d+\s+passed\b", output)
        )
        if is_test_action and passed:
            return int(step.get("step", 0))
    return None


def _render_report(records: list[dict[str, Any]], config: dict[str, Any]) -> str:
    baseline = [record for record in records if record["variant"] == "standard"]
    ablation = [record for record in records if record["variant"] != "standard"]
    providers = config["providers"]
    tasks = config["tasks"]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    commit_id = commit.stdout.strip() if commit.returncode == 0 else "unavailable"

    lines = [
        "# Agent Smith Model Benchmark Report",
        "",
        "## 1. Setup",
        "",
        f"- Repository commit: {commit_id}",
        f"- Run timestamp (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"- Providers: {len(providers)}",
        f"- Models: {sum(len(item['models']) for item in providers)}",
        f"- SWE-bench tasks: {len(tasks)}",
        "- All baseline models were run against the same task set.",
        "- API keys were loaded from environment variables; no keys are stored in results.",
        "",
        "| Provider | Provider URL | Models |",
        "| --- | --- | --- |",
    ]
    for provider in providers:
        lines.append(
            f"| {provider['name']} | {provider['provider_url']} | "
            f"{', '.join(provider['models'])} |"
        )

    lines.extend([
        "",
        "### Task selection",
        "",
        "| Task ID | Task file | Selection rationale |",
        "| --- | --- | --- |",
    ])
    for task in tasks:
        lines.append(
            f"| {task['resolved_task_id']} | {task['resolved_task_file']} | "
            f"{task['rationale']} |"
        )

    lines.extend([
        "",
        "## 2. Results",
        "",
        "| Provider | Model | Task | Pass/Fail | Iterations | Input tokens | Output tokens | Wall time (s) |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ])
    for record in baseline:
        result = record["result"]
        lines.append(
            f"| {record['provider']} | {record['model']} | {record['task_id']} | "
            f"{'Pass' if result.get('success') else 'Fail'} | "
            f"{result.get('iterations', 0)} | "
            f"{result.get('total_input_tokens', 0)} | "
            f"{result.get('total_output_tokens', 0)} | "
            f"{record['wall_seconds']:.2f} |"
        )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in baseline:
        grouped[(record["provider"], record["model"])].append(record)
    lines.extend([
        "",
        "## 3. Provider reliability",
        "",
        "| Provider | Model | Avg. response time/request (ms) | Retries | Availability |",
        "| --- | --- | ---: | ---: | ---: |",
    ])
    for (provider, model), model_records in sorted(grouped.items()):
        steps = [
            step
            for record in model_records
            for step in record["result"].get("steps", [])
        ]
        avg_ms = mean([step.get("request_time_ms", 0) for step in steps]) if steps else 0
        retries = sum(step.get("retries", 0) for step in steps)
        attempts = sum(record["result"].get("total_requests", 0) for record in model_records)
        availability = (len(steps) / attempts * 100) if attempts else 0
        lines.append(
            f"| {provider} | {model} | {avg_ms:.2f} | {retries} | {availability:.1f}% |"
        )

    lines.extend([
        "",
        "## 4. Intermediary metrics",
        "",
        "| Provider | Model | Task | First relevant file step | First passing test step | Iterations after first pass |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ])
    for record in baseline:
        result = record["result"]
        first_pass = _first_passing_test_step(result)
        after_pass = (
            str(max(0, int(result.get("iterations", 0)) - first_pass))
            if first_pass is not None
            else "N/A"
        )
        lines.append(
            f"| {record['provider']} | {record['model']} | {record['task_id']} | "
            f"{_first_relevant_file_step(result)} | "
            f"{first_pass if first_pass is not None else 'N/A'} | {after_pass} |"
        )

    lines.extend(["", "## 5. Ablation study", ""])
    if ablation:
        ablation_models = {(r["provider"], r["model"], r["variant"]) for r in ablation}
        for provider, model, variant in sorted(ablation_models):
            before = [
                r for r in baseline
                if r["provider"] == provider and r["model"] == model
            ]
            after = [
                r for r in ablation
                if r["provider"] == provider
                and r["model"] == model
                and r["variant"] == variant
            ]
            before_passes = sum(bool(r["result"].get("success")) for r in before)
            after_passes = sum(bool(r["result"].get("success")) for r in after)
            before_tokens = mean([
                r["result"].get("total_input_tokens", 0)
                + r["result"].get("total_output_tokens", 0)
                for r in before
            ]) if before else 0
            after_tokens = mean([
                r["result"].get("total_input_tokens", 0)
                + r["result"].get("total_output_tokens", 0)
                for r in after
            ]) if after else 0
            lines.extend([
                f"### {provider} / {model}: standard vs {variant}",
                "",
                "| Variant | Passed tasks | Avg. total tokens/task | Avg. wall time/task (s) |",
                "| --- | ---: | ---: | ---: |",
                f"| standard | {before_passes}/{len(before)} | {before_tokens:.1f} | "
                f"{mean([r['wall_seconds'] for r in before]) if before else 0:.2f} |",
                f"| {variant} | {after_passes}/{len(after)} | {after_tokens:.1f} | "
                f"{mean([r['wall_seconds'] for r in after]) if after else 0:.2f} |",
                "",
            ])
    else:
        lines.append("No ablation runs were recorded.")

    lines.extend(["", "## 6. Conclusions", ""])
    if grouped:
        ranked = sorted(
            grouped.items(),
            key=lambda item: (
                sum(bool(r["result"].get("success")) for r in item[1]) / max(len(item[1]), 1),
                -mean([r["wall_seconds"] for r in item[1]]),
            ),
            reverse=True,
        )
        provider_model, best_records = ranked[0]
        pass_count = sum(bool(r["result"].get("success")) for r in best_records)
        lines.append(
            f"- Highest measured baseline pass rate: {provider_model[0]} / "
            f"{provider_model[1]} ({pass_count}/{len(best_records)} tasks)."
        )
        lines.append(
            "- Highest pass rate is a measured candidate, not an automatic final selection; "
            "review traces, reliability, token use, and ablation before deciding."
        )
        zero_pass = [
            f"{provider}/{model}"
            for (provider, model), model_records in grouped.items()
            if not any(r["result"].get("success") for r in model_records)
        ]
        if zero_pass:
            lines.append(
                "- Models with zero passes on this task set (disregard candidates): "
                + ", ".join(sorted(zero_pass))
                + "."
            )
    else:
        lines.append("No completed runs were available.")
    lines.extend([
        "",
        "Review the generated solution.json traces before drawing final conclusions. "
        "The intermediary metrics are heuristic summaries and should be manually verified.",
        "",
        "## 7. Reproducibility",
        "",
        "Each run's backing solution.json is saved under the benchmark results directory. "
        "The task files, provider configuration, commit, model identifiers, and timestamps "
        "should be retained with the report.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SWE-bench model benchmark matrix.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", default="benchmark_results")
    parser.add_argument("--report", default="BENCHMARK_REPORT.md")
    parser.add_argument("--max-iterations", type=int, default=30)
    args = parser.parse_args(argv)

    try:
        if args.max_iterations < 1:
            parser.error("--max-iterations must be at least 1")
        config_path = Path(args.config).resolve()
        config = _load_config(config_path)
        output_root = Path(args.output_dir).resolve()
        records: list[dict[str, Any]] = []

        for provider in config["providers"]:
            for model_name in provider["models"]:
                for task in config["tasks"]:
                    output_path = (
                        output_root
                        / "standard"
                        / _safe_name(provider["name"])
                        / _safe_name(model_name)
                        / _safe_name(task["resolved_task_id"])
                        / "solution.json"
                    )
                    print(
                        f"[standard] {provider['name']} / {model_name} / "
                        f"{task['resolved_task_id']}"
                    )
                    records.append(_run_agent(
                        provider,
                        model_name,
                        task,
                        output_path,
                        "standard",
                        min(args.max_iterations, 30),
                    ))

        ablation = config["ablation"]
        provider = next(
            item for item in config["providers"]
            if item["name"] == ablation["provider_name"]
        )
        for task in config["tasks"]:
            output_path = (
                output_root
                / "ablation"
                / _safe_name(provider["name"])
                / _safe_name(ablation["model_name"])
                / _safe_name(task["resolved_task_id"])
                / "solution.json"
            )
            print(
                f"[ablation:{ablation['prompt_variant']}] "
                f"{provider['name']} / {ablation['model_name']} / "
                f"{task['resolved_task_id']}"
            )
            records.append(_run_agent(
                provider,
                ablation["model_name"],
                task,
                output_path,
                ablation["prompt_variant"],
                min(args.max_iterations, 30),
            ))

        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_render_report(records, config), encoding="utf-8")
        print(f"Benchmark report written to {report_path}")
        print(f"Backing solution files written under {output_root}")
        return 0
    except Exception as error:
        print(f"Benchmark runner failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
