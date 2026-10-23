import subprocess
import sys
import resource
import json
from sandbox.config import SandboxConfig
from sandbox.security import check_imports

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
                "--net",
                sys.executable,
                "-m",
                "sandbox.worker",
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=5,
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
