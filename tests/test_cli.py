from sandbox.__main__ import main


class FakeMCPClient:
    def __init__(self, stdio_command=None, server_url=None):
        self.stdio_command = stdio_command
        self.server_url = server_url
        self.tools = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def build_manual(self):
        return "fake MCP manual"

    def handle_call(self, method, params):
        return {}


def test_cli_loads_root_template_and_starts_repl(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sandbox.__main__.run_repl",
        lambda config, mcp_client=None: calls.append((config, mcp_client)) or 0,
    )

    assert main(["sandbox_template.json"]) == 0
    assert len(calls) == 1
    assert calls[0][0].max_execution_time_seconds == 30
    assert calls[0][1] is None


def test_cli_supports_mcp_stdio_before_config(monkeypatch):
    clients = []
    calls = []
    monkeypatch.setattr("sandbox.__main__.MCPClient", lambda **kwargs: clients.append(FakeMCPClient(**kwargs)) or clients[-1])
    monkeypatch.setattr(
        "sandbox.__main__.run_repl",
        lambda config, mcp_client=None: calls.append((config, mcp_client)) or 0,
    )

    assert main([
        "--mcp-stdio",
        "python mcp_tools_mbpp.py",
        "sandbox_template.json",
    ]) == 0
    assert clients[0].stdio_command == "python mcp_tools_mbpp.py"
    assert calls[0][1] is clients[0]


def test_cli_supports_streamable_http_mcp(monkeypatch):
    clients = []
    calls = []
    monkeypatch.setattr("sandbox.__main__.MCPClient", lambda **kwargs: clients.append(FakeMCPClient(**kwargs)) or clients[-1])
    monkeypatch.setattr(
        "sandbox.__main__.run_repl",
        lambda config, mcp_client=None: calls.append((config, mcp_client)) or 0,
    )

    assert main(["--mcp-server", "http://127.0.0.1:8000/mcp"]) == 0
    assert clients[0].server_url == "http://127.0.0.1:8000/mcp"
    assert calls[0][1] is clients[0]


def test_swebench_task_prompt_contains_real_line_breaks():
    from agent_swebench import build_task_prompt
    from models.tasks import SWEBenchTaskInput

    task = SWEBenchTaskInput(
        instance_id="example__repo-1",
        problem_statement="Fix the bug.",
        docker_image="example/image:latest",
        eval_script="pytest -q",
    )
    prompt = build_task_prompt(task)
    assert "Benchmark: SWE-bench\\nInstance" not in prompt
    assert "Benchmark: SWE-bench\nInstance" in prompt
