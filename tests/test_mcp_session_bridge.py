import json
from types import SimpleNamespace

from sandbox.config import SandboxConfig
from sandbox.executor import SandboxSession


def test_session_relays_mcp_calls_to_handler(monkeypatch):
    class FakeStdin:
        def __init__(self):
            self.writes = []

        def write(self, value):
            self.writes.append(value)

        def flush(self):
            pass

    class FakeStdout:
        def __init__(self):
            self.lines = iter([
                '{"status": "ready"}\n',
                json.dumps({
                    "status": "mcp_call",
                    "method": "call_tool",
                    "params": {"name": "sum", "arguments": {"a": 20, "b": 22}},
                }) + "\n",
                json.dumps({"status": "success", "output": ""}) + "\n",
            ])

        def readline(self):
            return next(self.lines, "")

        def fileno(self):
            return 1

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = SimpleNamespace(read=lambda: "")
            self.pid = 123
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0

    process = FakeProcess()
    monkeypatch.setattr("sandbox.executor.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("sandbox.executor.select.select", lambda *a, **k: ([process.stdout], [], []))

    calls = []
    def handler(method, params):
        calls.append((method, params))
        return {"total": params["arguments"]["a"] + params["arguments"]["b"]}

    session = SandboxSession(SandboxConfig(), mcp_handler=handler)
    try:
        response = session.execute("answer = sum(a=20, b=22)")
        assert response == {"status": "success", "output": ""}
        assert calls == [(
            "call_tool",
            {"name": "sum", "arguments": {"a": 20, "b": 22}},
        )]
        relayed = json.loads(process.stdin.writes[-1])
        assert relayed == {"status": "mcp_result", "result": {"total": 42}}
    finally:
        session.close()


def test_oversized_mcp_result_has_explicit_truncation_notice():
    from sandbox.executor import _truncate_mcp_result

    result = _truncate_mcp_result({"content": "x" * 100}, limit=20)
    assert result["_sandbox_truncated"] is True
    assert "truncated" in result["notice"]
    assert len(result["preview"]) == 20


def test_mcp_wait_is_excluded_but_code_time_accumulates(monkeypatch):
    class FakeStdin:
        def __init__(self):
            self.writes = []

        def write(self, value):
            self.writes.append(value)

        def flush(self):
            pass

    class FakeStdout:
        def __init__(self):
            self.lines = iter([
                '{"status": "ready"}\n',
                json.dumps({
                    "status": "mcp_call",
                    "method": "call_tool",
                    "params": {"name": "slow_tool", "arguments": {}},
                }) + "\n",
                json.dumps({"status": "success", "output": "done"}) + "\n",
            ])

        def readline(self):
            return next(self.lines, "")

        def fileno(self):
            return 1

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = SimpleNamespace(read=lambda: "")
            self.pid = 124
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0

    process = FakeProcess()
    time_values = iter([0.0, 0.7, 100.0, 100.4])
    select_timeouts = []
    monkeypatch.setattr("sandbox.executor.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("sandbox.executor.time.monotonic", lambda: next(time_values))

    def fake_select(readers, writers, errors, timeout):
        select_timeouts.append(timeout)
        return ([process.stdout], [], [])

    monkeypatch.setattr("sandbox.executor.select.select", fake_select)
    session = SandboxSession(
        SandboxConfig(max_execution_time_seconds=1),
        mcp_handler=lambda method, params: "tool completed",
    )
    try:
        response = session.execute("result = slow_tool()")
        assert response == {"status": "success", "output": "done"}
        assert select_timeouts[0] == 1
        assert 0.29 <= select_timeouts[1] <= 0.31
    finally:
        session.close()
