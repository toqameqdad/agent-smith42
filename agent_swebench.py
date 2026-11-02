"""CLI entry point for the SWE-bench autonomous coding agent."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from agent.orchestrator import AgentOrchestrator
from llm.client import LLMClient
from llm.providers import ProviderConfig
from models.tasks import SWEBenchTaskInput
from sandbox.config import SandboxConfig
from sandbox.executor import SandboxSession
from sandbox.mcp_client import MCPClient
from tools.docker_workspace import sync_workspace_from_container


def load_task(path: str) -> SWEBenchTaskInput:
    with Path(path).open(encoding="utf-8") as task_file:
        data = json.load(task_file)
    if isinstance(data, dict) and "task" in data and isinstance(data["task"], dict):
        data = data["task"]
    return SWEBenchTaskInput.model_validate(data)


def build_task_prompt(task: SWEBenchTaskInput) -> str:
    return (
        "Benchmark: SWE-bench\n"
        f"Instance: {task.instance_id}\n"
        f"Repository: {task.repo or '(not specified)'}\n\n"
        "Issue description:\n"
        f"{task.problem_statement}\n\n"
        "Hints:\n"
        f"{task.hints_text or '(none)'}\n\n"
        "The repository is available through the mandatory MCP tools. First inspect "
        "the relevant files and tests. Reproduce the issue, identify the root cause, "
        "make a minimal targeted change, and run the evaluation script. Do not fetch "
        "solutions from external sources. When the patch is ready, call "
        "final_answer(get_patch())."
    )


def _docker_run(image: str, container_name: str) -> None:
    result = subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", container_name,
            "--entrypoint", "/bin/bash",
            image,
            "-lc", "while true; do sleep 3600; done",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Failed to start SWE-bench container.")


def _docker_cleanup(container_name: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent Smith SWE-bench agent.")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--provider-url", required=True)
    parser.add_argument("--provider-name", default="default")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--max-iterations", type=int, default=30)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))

    container_name = "agent-smith-" + re.sub(
        r"[^a-zA-Z0-9_.-]", "-", Path(args.task_file).stem
    )[:35] + "-" + uuid.uuid4().hex[:8]
    old_environment = {
        name: os.environ.get(name)
        for name in ("DOCKER_CONTAINER", "TESTBED_PATH", "EVAL_SCRIPT")
    }
    llm = None

    try:
        task = load_task(args.task_file)
        provider = ProviderConfig(
            name=args.provider_name,
            base_url=args.provider_url,
            model_name=args.model_name,
            api_key_env=[
                name.strip()
                for name in args.api_key_env.split(",")
                if name.strip()
            ],
        )
        llm = LLMClient([provider])
        _docker_run(task.docker_image, container_name)

        with tempfile.TemporaryDirectory(prefix="agent-smith-swe-") as workspace:
            workspace_path = Path(workspace)
            sync_workspace_from_container(container_name, workspace_path)
            os.environ["DOCKER_CONTAINER"] = container_name
            os.environ["TESTBED_PATH"] = str(workspace_path)
            os.environ["EVAL_SCRIPT"] = task.eval_script

            mcp_command = f"{sys.executable} {Path(__file__).with_name('mcp_tools_swebench.py')}"
            with MCPClient(stdio_command=mcp_command) as mcp_client:
                sandbox_config = SandboxConfig(
                    max_execution_time_seconds=30,
                    max_memory_mb=512,
                )
                with SandboxSession(
                    sandbox_config,
                    mcp_tools=mcp_client.tools,
                    mcp_manual=mcp_client.build_manual(),
                    mcp_handler=mcp_client.handle_call,
                ) as sandbox:
                    agent = AgentOrchestrator(
                        llm_client=llm,
                        sandbox=sandbox,
                        provider_name=args.provider_name,
                        model_name=args.model_name,
                        api_url=args.provider_url,
                        sandbox_manual=mcp_client.build_manual(),
                        max_iterations=args.max_iterations,
                        max_input_tokens=300000,
                        max_output_tokens=10000,
                        timeout_seconds=900,
                    )
                    result = agent.run(
                        task_id=task.instance_id,
                        benchmark="swebench",
                        task_prompt=build_task_prompt(task),
                    )

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(result.model_dump_json(indent=2))
        return 0
    except Exception as error:
        print(f"SWE-bench agent failed: {error}", file=sys.stderr)
        return 1
    finally:
        if llm is not None:
            llm.close()
        _docker_cleanup(container_name)
        for name, value in old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
