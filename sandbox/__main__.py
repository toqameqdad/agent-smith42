import argparse
import codeop
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from sandbox.config import SandboxConfig
from sandbox.executor import SandboxSession
from sandbox.mcp_client import MCPClient


def load_config(config_path: str | None) -> SandboxConfig:
    """Load sandbox settings from JSON or use the defaults."""
    if config_path is None:
        return SandboxConfig()

    path = Path(config_path)
    try:
        with path.open(encoding="utf-8") as config_file:
            data = json.load(config_file)
        return SandboxConfig.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"Invalid sandbox configuration: {error}") from error


def run_repl(config: SandboxConfig, mcp_client: MCPClient | None = None) -> int:
    """Run a persistent, isolated Python REPL."""
    print("Agent Smith sandbox. Type 'exit' or press Ctrl+D to quit.")

    try:
        with SandboxSession(
            config,
            mcp_tools=mcp_client.tools if mcp_client else [],
            mcp_manual=mcp_client.build_manual() if mcp_client else "",
            mcp_handler=mcp_client.handle_call if mcp_client else None,
        ) as session:
            lines: list[str] = []

            while True:
                prompt = "... " if lines else ">>> "
                try:
                    line = input(prompt)
                except EOFError:
                    print()
                    break

                if not lines and line.strip() == "exit":
                    break

                lines.append(line)
                source = "\n".join(lines)

                try:
                    compiled = codeop.compile_command(source, symbol="exec")
                except (SyntaxError, OverflowError, ValueError):
                    compiled = True

                if compiled is None:
                    continue

                response = session.execute(source)
                lines.clear()

                output = response.get("output", "")
                if output:
                    print(output, end="")

                if response.get("status") == "error":
                    print(f"Error: {response.get('message', 'Unknown error')}")
                elif response.get("status") == "final_answer":
                    print(f"Final answer: {response.get('answer', '')}")

                if session.process.poll() is not None:
                    break

    except (OSError, RuntimeError, ValueError) as error:
        print(f"Sandbox error: {error}", file=sys.stderr)
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sandbox",
        description="Run code in the Agent Smith isolated sandbox.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        help="Optional JSON sandbox configuration file.",
    )
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--mcp-stdio",
        metavar="COMMAND",
        help="Connect to an MCP server launched by a stdio command.",
    )
    transport.add_argument(
        "--mcp-server",
        metavar="URL",
        help="Connect to an MCP server using Streamable HTTP.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    if not args.mcp_stdio and not args.mcp_server:
        return run_repl(config)

    try:
        with MCPClient(
            stdio_command=args.mcp_stdio,
            server_url=args.mcp_server,
        ) as mcp_client:
            print(mcp_client.build_manual())
            return run_repl(config, mcp_client)
    except Exception as error:
        print(f"MCP connection error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
