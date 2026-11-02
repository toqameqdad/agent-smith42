"""CLI entry point for the MBPP autonomous coding agent."""

import argparse
import json
import sys
from pathlib import Path

from agent.orchestrator import AgentOrchestrator
from llm.client import LLMClient
from llm.providers import ProviderConfig
from models.tasks import MBPPTaskInput
from sandbox.config import SandboxConfig
from sandbox.executor import SandboxSession
from sandbox.mcp_client import MCPClient


def load_task(path: str) -> MBPPTaskInput:
    with Path(path).open(encoding="utf-8") as task_file:
        data = json.load(task_file)
    if isinstance(data, dict) and "task" in data and isinstance(data["task"], dict):
        data = data["task"]
    return MBPPTaskInput.model_validate(data)


def build_task_prompt(task: MBPPTaskInput) -> str:
    imports = "\n".join(task.test_imports)
    tests = "\n".join(task.test_list)
    return (
        "Benchmark: MBPP\n"
        f"Task ID: {task.task_id}\n\n"
        "Task definition:\n"
        f"{task.task_definition}\n\n"
        "Required function signature:\n"
        f"{task.function_definition}\n\n"
        "Test imports:\n"
        f"{imports or '(none)'}\n\n"
        "Test assertions:\n"
        f"{tests or '(none)'}\n\n"
        "Explore and solve the task. Use run_tests(code, test_list) to validate "
        "candidate code. When every supplied assertion passes, call "
        "final_answer(the_complete_python_solution_as_a_string)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent Smith MBPP agent.")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--provider-url", required=True)
    parser.add_argument("--provider-name", default="default")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--max-iterations", type=int, default=10)
    args = parser.parse_args(argv)

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
        mcp_command = f"{sys.executable} mcp_tools_mbpp.py"

        with MCPClient(stdio_command=mcp_command) as mcp_client:
            sandbox_config = SandboxConfig(
                max_execution_time_seconds=20,
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
                    max_input_tokens=6000,
                    max_output_tokens=1500,
                    timeout_seconds=120,
                )
                result = agent.run(
                    task_id=task.task_id,
                    benchmark="mbpp",
                    task_prompt=build_task_prompt(task),
                )
        llm.close()

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(result.model_dump_json(indent=2))
        return 0
    except Exception as error:
        print(f"MBPP agent failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
