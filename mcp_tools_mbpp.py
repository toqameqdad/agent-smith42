"""MCP server exposing the MBPP candidate-test tool."""

import argparse
import json

from mcp.server import MCPServer

from sandbox.config import SandboxConfig
from sandbox.executor import send_to_worker


mcp = MCPServer("Agent Smith MBPP tools")


@mcp.tool()
def run_tests(code: str, test_list: list[str]) -> str:
    """Run a candidate Python solution against MBPP assertion tests in the sandbox."""
    if not code.strip():
        return json.dumps({"success": False, "output": "Candidate code is empty."})
    if not test_list:
        return json.dumps({"success": False, "output": "No test assertions were supplied."})

    test_source = "\n".join(test_list)
    complete_source = (
        code.rstrip()
        + "\n\n"
        + test_source
        + "\nprint('MBPP_TESTS_PASSED')\n"
    )
    result = send_to_worker(
        complete_source,
        SandboxConfig(
            max_execution_time_seconds=15,
            max_memory_mb=512,
        ),
    )
    output = result.get("output", "")
    if result.get("status") == "success" and "MBPP_TESTS_PASSED" in output:
        return json.dumps({"success": True, "output": output})
    return json.dumps({
        "success": False,
        "output": output or result.get("message", "Tests failed."),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MBPP MCP tools server.")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
