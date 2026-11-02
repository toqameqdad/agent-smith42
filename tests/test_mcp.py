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


def test_mbpp_tool_runs_candidate_in_sandbox(monkeypatch):
    import json
    import mcp_tools_mbpp

    calls = []
    monkeypatch.setattr(
        mcp_tools_mbpp,
        "send_to_worker",
        lambda code, config: calls.append((code, config)) or {
            "status": "success",
            "output": "MBPP_TESTS_PASSED\n",
        },
    )

    result = json.loads(
        mcp_tools_mbpp.run_tests(
            "def add(a, b): return a + b",
            ["assert add(1, 2) == 3"],
        )
    )
    assert result["success"] is True
    assert "assert add(1, 2) == 3" in calls[0][0]


def test_mbpp_tool_rejects_empty_inputs():
    import json
    from mcp_tools_mbpp import run_tests

    result = json.loads(run_tests("", ["assert True"]))
    assert result["success"] is False
    assert "empty" in result["output"]


def test_swebench_mcp_server_supports_streamable_http(tmp_path, monkeypatch):
    import socket
    import subprocess
    import sys
    import time

    (tmp_path / "example.py").write_text("answer = 42\n", encoding="utf-8")
    monkeypatch.setenv("TESTBED_PATH", str(tmp_path))

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    process = subprocess.Popen(
        [
            sys.executable,
            "mcp_tools_swebench.py",
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        last_error = None
        for _ in range(40):
            if process.poll() is not None:
                raise RuntimeError(process.stderr.read())
            try:
                with MCPClient(server_url=f"http://127.0.0.1:{port}/mcp") as client:
                    names = {tool["name"] for tool in client.tools}
                    assert "read_file" in names
                    result = client.handle_call(
                        "call_tool",
                        {
                            "name": "read_file",
                            "arguments": {"filepath": "example.py"},
                        },
                    )
                    assert "answer = 42" in str(result)
                    return
            except Exception as error:
                last_error = error
                time.sleep(0.25)
        raise AssertionError(f"Streamable HTTP MCP server did not become ready: {last_error}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
