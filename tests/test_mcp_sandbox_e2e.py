import subprocess
import sys

import pytest

from sandbox.config import SandboxConfig
from sandbox.executor import SandboxSession
from sandbox.mcp_client import MCPClient


def test_interactive_session_calls_mcp_server_end_to_end(tmp_path):
    namespace_probe = subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--net",
            "true",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if namespace_probe.returncode != 0:
        pytest.skip(
            "Linux namespaces are unavailable: "
            + namespace_probe.stderr.strip()
        )

    server_file = tmp_path / "mcp_server.py"
    server_file.write_text(
        "from pydantic import BaseModel\n"
        "from mcp.server import MCPServer\n"
        "class Sum(BaseModel):\n"
        "    result: int\n"
        "mcp = MCPServer('Agent Smith sandbox integration')\n"
        "@mcp.tool()\n"
        "def add(a: int, b: int) -> Sum:\n"
        "    return Sum(result=a + b)\n"
        "if __name__ == '__main__':\n"
        "    mcp.run()\n",
        encoding="utf-8",
    )

    with MCPClient(
        stdio_command=f'"{sys.executable}" "{server_file}"'
    ) as client:
        config = SandboxConfig(max_execution_time_seconds=10)
        with SandboxSession(
            config,
            mcp_tools=client.tools,
            mcp_manual=client.build_manual(),
            mcp_handler=client.handle_call,
        ) as session:
            response = session.execute(
                "result = add(a=20, b=22)\n"
                "print(result['structuredContent']['result'])"
            )

    assert response["status"] == "success", response
    assert response["output"] == "42\n"
