
import json
import sys
import subprocess
import resource
from pathlib import Path

from pydantic import ValidationError

from sandbox.config import SandboxConfig
from sandbox.environment import (
    prepare_python_runtime,
    get_shared_libraries,
    prepare_library_mount_points,
    build_chroot_command,
)
from sandbox.security import check_imports


SANDBOX_ROOT = Path("/tmp/agent-smith-runtime")


def is_runtime_ready(root: Path) -> bool:
    required_paths = [
        root / "bin" / "python3.10",
        root / "lib" / "python3.10" / "encodings",
        root / "lib" / "python3.10" / "lib-dynload",
        root / "install",
    ]

    return (
        all(path.exists() for path in required_paths)
        and (root / "bin" / "python3.10").is_file()
        and (root / "lib" / "python3.10" / "encodings").is_dir()
        and (root / "lib" / "python3.10" / "lib-dynload").is_dir()
        and (root / "install").is_symlink()
    )


def set_memory_limit(memory_mb: int) -> None:
    memory_bytes = memory_mb * 1024 * 1024
    resource.setrlimit(
        resource.RLIMIT_AS,
        (memory_bytes, memory_bytes),
    )


def mount_runtime_libraries(root: Path) -> None:
    subprocess.run(
        ["mount", "--make-rprivate", "/"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    python_executable = root / "bin" / "python3.10"

    libraries = get_shared_libraries(str(python_executable))
    libraries.append("/lib/x86_64-linux-gnu/libseccomp.so.2")
    mount_points = prepare_library_mount_points(root, libraries)

    for source, destination in mount_points:
        subprocess.run(
            ["mount", "--bind", str(source), str(destination)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )


def execute_in_sandbox(
    code: str,
    config: SandboxConfig,
) -> dict:
    if not check_imports(code, config.authorized_imports):
        return {
            "status": "error",
            "message": "Execution blocked: unauthorized import or invalid syntax.",
        }

    try:
        prepare_python_runtime(SANDBOX_ROOT)

        if not is_runtime_ready(SANDBOX_ROOT):
            raise RuntimeError("Python runtime is not ready.")

        mount_runtime_libraries(SANDBOX_ROOT)

        code = "import ctypes; ctypes.CDLL('libseccomp.so.2'); print('libseccomp loaded')"
        command = build_chroot_command(SANDBOX_ROOT, code)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.max_execution_time_seconds,
            preexec_fn=lambda: set_memory_limit(config.max_memory_mb),
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "message": result.stderr.strip(),
                "output": result.stdout,
            }

        return {
            "status": "success",
            "output": result.stdout,
        }

    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": "Execution timed out.",
        }

    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        return {
            "status": "error",
            "message": str(error),
        }


def main() -> None:
    try:
        message = sys.stdin.read()
        data = json.loads(message)

        code = data["code"]
        config = SandboxConfig.model_validate(data["config"])

        if not isinstance(code, str):
            raise ValueError("Code must be a string.")

        response = execute_in_sandbox(code, config)

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        response = {
            "status": "error",
            "message": str(error),
        }

    print(json.dumps(response))


if __name__ == "__main__":
    main()
