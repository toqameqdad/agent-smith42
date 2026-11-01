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


def build_bootstrap_code(
    authorized_imports: list[str] | None = None,
    allowed_directories: list[str] | None = None,
) -> str:
    """
    Build trusted bootstrap code for execution inside chroot.
    """

    default_config = SandboxConfig()
    if authorized_imports is None:
        authorized_imports = default_config.authorized_imports
    if allowed_directories is None:
        allowed_directories = default_config.allowed_directories

    bootstrap_path = Path(__file__).with_name("bootstrap.py")
    security_path = Path(__file__).with_name("security.py")

    bootstrap_source = bootstrap_path.read_text(encoding="utf-8")
    security_source = security_path.read_text(encoding="utf-8")

    return (
        "import sys\n"
        f"bootstrap_source = {bootstrap_source!r}\n"
        f"security_source = {security_source!r}\n"
        f"authorized_imports = {authorized_imports!r}\n"
        f"allowed_directories = {allowed_directories!r}\n"
        "namespace = {'__name__': '_trusted_bootstrap'}\n"
        "exec(compile(bootstrap_source, '<trusted-bootstrap>', 'exec'), namespace)\n"
        "user_code = sys.stdin.read()\n"
        "namespace['run'](user_code, security_source, authorized_imports, allowed_directories)\n"
    )


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


def _run_mount_command(arguments: list[str]) -> None:
    subprocess.run(
        ["mount", *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def mount_runtime_libraries(
    root: Path,
    allowed_directories: list[str],
) -> None:
    """
    Mount the runtime read-only and expose only configured writable directories.

    This function must run inside the worker's private mount namespace.
    """
    root = root.resolve(strict=True)

    _run_mount_command(["--make-rprivate", "/"])
    _run_mount_command(["--bind", str(root), str(root)])

    python_executable = root / "bin" / "python3.10"
    libraries = get_shared_libraries(str(python_executable))
    seccomp_library = "/lib/x86_64-linux-gnu/libseccomp.so.2"
    if seccomp_library not in libraries:
        libraries.append(seccomp_library)

    mount_points = prepare_library_mount_points(root, libraries)
    for source, destination in mount_points:
        _run_mount_command(["--bind", str(source), str(destination)])
        _run_mount_command([
            "-o", "remount,bind,ro", str(destination)
        ])

    writable_targets: list[Path] = []
    for directory in allowed_directories:
        configured_path = Path(directory)
        if not configured_path.is_absolute() or ".." in configured_path.parts:
            raise ValueError(
                f"Allowed directory must be an absolute sandbox path: {directory}"
            )

        target = root / configured_path.relative_to("/")
        try:
            resolved_target = target.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(
                f"Allowed directory does not exist: {directory}"
            ) from error

        if (
            resolved_target != target
            or resolved_target == root
            or not resolved_target.is_relative_to(root)
            or not resolved_target.is_dir()
        ):
            raise ValueError(
                f"Invalid allowed directory inside sandbox: {directory}"
            )

        if any(
            resolved_target == existing
            or resolved_target.is_relative_to(existing)
            or existing.is_relative_to(resolved_target)
            for existing in writable_targets
        ):
            raise ValueError(
                f"Overlapping allowed directories are not supported: {directory}"
            )

        writable_targets.append(resolved_target)

    for target in writable_targets:
        _run_mount_command(["--bind", str(target), str(target)])

    _run_mount_command([
        "-o", "remount,bind,ro", str(root)
    ])

    for target in writable_targets:
        _run_mount_command([
            "-o", "remount,bind,rw", str(target)
        ])


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

        mount_runtime_libraries(SANDBOX_ROOT, config.allowed_directories)

        bootstrap_code = build_bootstrap_code(
            config.authorized_imports,
            config.allowed_directories,
        )
        command = build_chroot_command(SANDBOX_ROOT, bootstrap_code)

        result = subprocess.run(
            command,
            input=code,
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
