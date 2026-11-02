import subprocess
import sys
import resource
import json
import os
import signal
import select
from sandbox.config import SandboxConfig
from sandbox.security import check_imports


def _truncate_mcp_result(result: object, limit: int = 20000) -> object:
    """Bound MCP payload size while preserving an explicit truncation notice."""
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= limit:
        return result
    return {
        "_sandbox_truncated": True,
        "notice": (
            f"MCP output exceeded {limit} characters and was truncated. "
            "The preview is incomplete; request a narrower result."
        ),
        "preview": serialized[:limit],
    }


def set_memory_limit(memory_mb: int) -> None:
    """
    Set a memory limit for the current process.

    Args:
        memory_mb (int): The memory limit in megabytes.
    """
    memory_bytes = memory_mb * 1024 * 1024

    resource.setrlimit(
        resource.RLIMIT_AS,
        (memory_bytes, memory_bytes)
    )  


def execute_code(
    code: str,
    config: SandboxConfig,
    ) -> str:
    """
    Execute the provided code in a subprocess and return the output.

    Args:
        code (str): The code to execute.
        timeout (int): The maximum time in seconds to allow for execution.
        memory_mb (int): The memory limit in megabytes.

    Returns:
        str: The standard output or an error message.
    """
    if not check_imports(code, config.authorized_imports):
        return "Execution blocked: unauthorized import or invalid syntax"
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=config.max_execution_time_seconds,
            preexec_fn=lambda: set_memory_limit(config.max_memory_mb)
        )

        if result.returncode != 0:
            return result.stderr

        return result.stdout

    except subprocess.TimeoutExpired:
        return "Execution timed out."

    except OSError as e:
        return f"Execution failed: {e}"



def send_to_worker(
    code: str,
    config: SandboxConfig
) -> dict:
    """
    Send a JSON request to an isolated worker.

    Args:
        code (str): Python source code to send.
        config (SandboxConfig): Worker configuration.

    Returns:
        dict: Worker response or a structured error.
    """
    request = {
        "code": code,
        "config": config.model_dump()
    }

    try:
        result = subprocess.run(
            [
                "unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--net",
                sys.executable,
                "-m",
                "sandbox.worker",
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=config.max_execution_time_seconds + 15,
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "message": result.stderr.strip()
            }

        return json.loads(result.stdout)

    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": "Worker timed out."
        }

    except (OSError, json.JSONDecodeError) as error:
        return {
            "status": "error",
            "message": str(error)
        }



class SandboxSession:
    """Persistent sandbox process used by the interactive REPL."""

    def __init__(
        self,
        config: SandboxConfig,
        mcp_tools: list[dict] | None = None,
        mcp_manual: str = "",
        mcp_handler=None,
    ):
        self.config = config
        self.mcp_handler = mcp_handler
        self.process = subprocess.Popen(
            [
                "unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--net",
                sys.executable,
                "-m",
                "sandbox.worker",
                "--interactive",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )

        self.process.stdin.write(
            json.dumps({
                "config": config.model_dump(),
                "mcp_tools": mcp_tools or [],
                "mcp_manual": mcp_manual,
            }) + "\n"
        )
        self.process.stdin.flush()

        ready_line = self.process.stdout.readline()
        if not ready_line:
            error = self.process.stderr.read().strip()
            self._terminate()
            raise RuntimeError(error or "Sandbox session failed to start.")

        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as error:
            self._terminate()
            raise RuntimeError("Invalid sandbox startup response.") from error

        if ready.get("status") != "ready":
            self._terminate()
            raise RuntimeError(
                ready.get("message", "Sandbox session failed to start.")
            )

    def execute(self, code: str) -> dict:
        """Execute one entry while preserving the session namespace."""
        if self.process.poll() is not None:
            return {
                "status": "error",
                "message": "Sandbox session is no longer running.",
            }

        request = {"code": code}
        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()

            while True:
                ready, _, _ = select.select(
                    [self.process.stdout],
                    [],
                    [],
                    self.config.max_execution_time_seconds,
                )
                if not ready:
                    self._terminate()
                    return {
                        "status": "error",
                        "message": (
                            "Execution timed out; no complete output was available. "
                            "Any returned output may be partial."
                        ),
                        "output": "",
                    }

                response_line = self.process.stdout.readline()
                if not response_line:
                    error = self.process.stderr.read().strip()
                    return {
                        "status": "error",
                        "message": error or "Sandbox process exited.",
                    }

                response = json.loads(response_line)
                if response.get("status") != "mcp_call":
                    return response

                try:
                    if self.mcp_handler is None:
                        raise RuntimeError("No MCP server is connected.")
                    result = self.mcp_handler(
                        response["method"],
                        response.get("params", {}),
                    )
                    mcp_response = {
                        "status": "mcp_result",
                        "result": _truncate_mcp_result(result),
                    }
                except Exception as error:
                    mcp_response = {
                        "status": "mcp_result",
                        "error": str(error),
                    }

                self.process.stdin.write(json.dumps(mcp_response) + "\n")
                self.process.stdin.flush()

        except (BrokenPipeError, OSError, json.JSONDecodeError) as error:
            self._terminate()
            return {
                "status": "error",
                "message": str(error),
            }

    def _terminate(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()

    def close(self) -> None:
        """Close the persistent sandbox process."""
        if self.process.poll() is None:
            try:
                self.process.stdin.write(
                    json.dumps({"command": "close"}) + "\n"
                )
                self.process.stdin.flush()
                self.process.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self._terminate()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
