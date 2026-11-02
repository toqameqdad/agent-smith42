import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from sandbox.mcp_client import MCPClient
from tools.docker_workspace import sync_workspace_from_container
from tools.execution import get_patch, run_command, run_tests
from tools.filesystem import edit_file, read_file


def test_swebench_tools_bridge_to_real_docker_workspace(monkeypatch):
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is unavailable.")
    probe = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode:
        pytest.skip("Docker daemon is unavailable.")

    container = "agent-smith-tools-" + uuid.uuid4().hex[:8]
    started = subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", container,
            "--entrypoint", "/bin/bash",
            "python:3.10-slim",
            "-lc", "while true; do sleep 3600; done",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if started.returncode:
        pytest.fail(started.stderr)

    try:
        setup = subprocess.run(
            [
                "docker", "exec", container, "/bin/bash", "-lc",
                "apt-get update -qq && apt-get install -y -qq git && "
                "mkdir -p /testbed && cd /testbed && git init -q && "
                "git config user.email agent@example.test && "
                "git config user.name 'Agent Smith' && "
                "printf 'def answer():\\n    return 1\\n' > sample.py && "
                "git add sample.py && git commit -qm initial",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if setup.returncode:
            pytest.fail(setup.stderr)

        with tempfile.TemporaryDirectory(prefix="agent-smith-docker-test-") as workspace:
            workspace_path = Path(workspace)
            sync_workspace_from_container(container, workspace_path)
            monkeypatch.setenv("DOCKER_CONTAINER", container)
            monkeypatch.setenv("TESTBED_PATH", str(workspace_path))
            monkeypatch.setenv("EVAL_SCRIPT", "python -c \"print('evaluation passed')\"")

            assert "return 1" in read_file("/testbed/sample.py")
            feedback = edit_file(
                "/testbed/sample.py",
                "return 1",
                "return 42",
            )
            assert "Updated" in feedback

            command_result = run_command("cat sample.py", "/testbed")
            assert "return 42" in command_result["stdout"]

            evaluation = run_tests()
            assert evaluation["exit_code"] == 0
            assert "evaluation passed" in evaluation["stdout"]

            patch = get_patch()
            assert "+    return 42" in patch

            with MCPClient(
                stdio_command=f"{sys.executable} mcp_tools_swebench.py"
            ) as client:
                response = client.handle_call(
                    "call_tool",
                    {
                        "name": "read_file",
                        "arguments": {"filepath": "/testbed/sample.py"},
                    },
                )
                assert "return 42" in str(response)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
