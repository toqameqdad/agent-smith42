"""MCP server exposing the mandatory SWE-bench repository tools."""

import argparse

from mcp.server import MCPServer

from tools.code_search import (
    find_references as _find_references,
    search_code as _search_code,
    search_function_or_class_definition_in_code as _search_definition,
)
from tools.execution import (
    get_patch as _get_patch,
    run_command as _run_command,
    run_tests as _run_tests,
)
from tools.filesystem import (
    edit_file as _edit_file,
    list_files as _list_files,
    read_file as _read_file,
)


mcp = MCPServer("Agent Smith SWE-bench tools")


@mcp.tool()
def read_file(filepath: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a repository file and return numbered lines."""
    return _read_file(filepath, start_line, None if end_line <= 0 else end_line)


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """Replace one unique exact string in a repository file."""
    return _edit_file(filepath, old_str, new_str)


@mcp.tool()
def list_files(directory: str = ".", pattern: str = "*") -> str:
    """List repository files under a directory matching a filename pattern."""
    return _list_files(directory, pattern)


@mcp.tool()
def search_code(pattern: str, file_pattern: str = "*") -> str:
    """Search repository source lines using a regular expression."""
    return _search_code(pattern, file_pattern)


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find Python function, async-function, and class definitions by name."""
    return _search_definition(name)


@mcp.tool()
def find_references(name: str, filepath: str = "", line: int = 0) -> str:
    """Find occurrences of a symbol throughout the repository."""
    return _find_references(name, filepath, line)


@mcp.tool()
def run_tests() -> dict:
    """Execute the evaluation script configured for the task repository."""
    return _run_tests()


@mcp.tool()
def get_patch() -> str:
    """Return the unified git diff for all tracked working-tree changes."""
    return _get_patch()


@mcp.tool()
def run_command(command: str, workdir: str = ".") -> dict:
    """Run a shell command in a directory inside the task repository."""
    return _run_command(command, workdir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SWE-bench MCP tools server.")
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
