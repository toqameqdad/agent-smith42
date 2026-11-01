import json
import subprocess
import sys

from sandbox.worker import build_interactive_bootstrap_code


def test_interactive_mcp_request_response_round_trip():
    source = build_interactive_bootstrap_code(
        ["math"],
        ["/testbed", "/tmp/agent"],
    )
    source = source.replace(
        "security_namespace['apply_worker_seccomp_policy']()",
        "pass",
    )
    source = source.replace(
        "security_namespace['apply_landlock'](allowed_directories, ['/bin', '/lib', '/lib64', '/usr/lib', '/usr/lib64', '/usr/local/lib'])",
        "pass",
    )

    process = subprocess.Popen(
        [sys.executable, "-B", "-c", source],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        ready = json.loads(process.stdout.readline())
        assert ready == {"status": "ready"}

        process.stdin.write(json.dumps({
            "code": "answer = call_mcp_tool('sum', a=20, b=22)"
        }) + "\n")
        process.stdin.flush()

        request = json.loads(process.stdout.readline())
        assert request == {
            "status": "mcp_call",
            "method": "call_tool",
            "params": {
                "name": "sum",
                "arguments": {"a": 20, "b": 22},
            },
        }

        process.stdin.write(json.dumps({
            "status": "mcp_result",
            "result": {"total": 42},
        }) + "\n")
        process.stdin.flush()

        response = json.loads(process.stdout.readline())
        assert response == {"status": "success", "output": ""}

        process.stdin.write(json.dumps({
            "code": "print(answer['total']); final_answer('done')"
        }) + "\n")
        process.stdin.flush()

        final = json.loads(process.stdout.readline())
        assert final == {
            "status": "final_answer",
            "answer": "done",
            "output": "42\\n",
        }
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
