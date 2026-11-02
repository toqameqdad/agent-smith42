import sys

from sandbox.mcp_client import MCPClient


def test_swebench_mcp_server_exposes_mandatory_tools(tmp_path, monkeypatch):
    (tmp_path / "example.py").write_text("answer = 42\n", encoding="utf-8")
    monkeypatch.setenv("TESTBED_PATH", str(tmp_path))

    with MCPClient(stdio_command=f"{sys.executable} mcp_tools_swebench.py") as client:
        names = {tool["name"] for tool in client.tools}
        assert names == {
            "read_file",
            "edit_file",
            "list_files",
            "search_code",
            "search_function_or_class_definition_in_code",
            "find_references",
            "run_tests",
            "get_patch",
            "run_command",
        }

        result = client.handle_call(
            "call_tool",
            {
                "name": "read_file",
                "arguments": {"filepath": "example.py", "start_line": 1},
            },
        )
        serialized = str(result)
        assert "answer = 42" in serialized
