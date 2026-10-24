
import json
import sys

from pydantic import ValidationError
from pathlib import Path
from sandbox.config import SandboxConfig


def is_runtime_ready(root: Path) -> bool:
    """
    Check whether the experimental Python runtime files exist.

    Args:
        root (Path): Path to the sandbox root.

    Returns:
        bool: True if the required runtime paths exist.
    """
    required_paths = [
        root / "bin" / "python3.10",
        root / "install" / "lib" / "python3.10" / "encodings",
    ]

    return all(path.exists() for path in required_paths)


def main() -> None:
    """
    Receive and validate a JSON request from the executor.

    The received Python code is not executed.

    Returns:
        None: Prints a JSON response to stdout.
    """
    try:
        message = sys.stdin.read()
        data = json.loads(message)

        code = data["code"]
        config = SandboxConfig.model_validate(data["config"])

        if not isinstance(code, str):
            raise ValueError("Code must be a string.")

        response = {
            "status": "received",
            "code": code,
            "timeout": config.max_execution_time_seconds,
            "memory_mb": config.max_memory_mb
        }

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError
    ) as error:
        response = {
            "status": "error",
            "message": str(error)
        }

    print(json.dumps(response))


if __name__ == "__main__":
    main()
