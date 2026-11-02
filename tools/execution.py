"""Execution and patch tools for a SWE-bench task workspace."""

import os
import subprocess

from tools.docker_workspace import docker_exec, sync_workspace_from_container
from tools.filesystem import repository_root, resolve_task_path


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _result(stdout: str, stderr: str, exit_code: int) -> dict:
    return {
        "stdout": _as_text(stdout),
        "stderr": _as_text(stderr),
        "exit_code": exit_code,
    }


def run_command(command: str, workdir: str = ".") -> dict:
    root = repository_root()
    cwd = resolve_task_path(workdir, root)
    if not cwd.is_dir():
        raise NotADirectoryError(workdir)
    timeout = float(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
    container = os.getenv("DOCKER_CONTAINER")
    if container:
        relative = cwd.relative_to(root)
        remote_cwd = "/testbed" if str(relative) == "." else f"/testbed/{relative}"
        try:
            result = docker_exec(
                container,
                ["/bin/bash", "-lc", command],
                timeout=timeout,
                workdir=remote_cwd,
            )
            sync_workspace_from_container(container, root)
            return _result(result.stdout, result.stderr, result.returncode)
        except subprocess.TimeoutExpired as error:
            return _result(_as_text(error.stdout), _as_text(error.stderr) + "\nCommand timed out.", 124)

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return _result(result.stdout, result.stderr, result.returncode)
    except subprocess.TimeoutExpired as error:
        return _result(
            _as_text(error.stdout),
            _as_text(error.stderr) + "\nCommand timed out.",
            124,
        )


def run_tests() -> dict:
    script = os.getenv("EVAL_SCRIPT", "")
    script_path = os.getenv("EVAL_SCRIPT_PATH", "")
    if script_path:
        path = resolve_task_path(script_path)
        if not path.is_file():
            raise FileNotFoundError(script_path)
        script = path.read_text(encoding="utf-8")
    if not script:
        return _result(
            "",
            "No evaluation script configured (EVAL_SCRIPT or EVAL_SCRIPT_PATH).",
            2,
        )

    timeout = float(os.getenv("TEST_TIMEOUT_SECONDS", "600"))
    container = os.getenv("DOCKER_CONTAINER")
    try:
        if container:
            result = docker_exec(
                container,
                ["/bin/bash", "-lc", script],
                timeout=timeout,
            )
            sync_workspace_from_container(container, repository_root())
        else:
            result = subprocess.run(
                script,
                cwd=repository_root(),
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        return _result(result.stdout, result.stderr, result.returncode)
    except subprocess.TimeoutExpired as error:
        return _result(_as_text(error.stdout), _as_text(error.stderr) + "\nTests timed out.", 124)


def get_patch() -> str:
    container = os.getenv("DOCKER_CONTAINER")
    if container:
        result = docker_exec(
            container,
            ["git", "-c", "core.fileMode=false", "diff"],
        )
    else:
        result = subprocess.run(
            ["git", "-c", "core.fileMode=false", "diff"],
            cwd=repository_root(),
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git diff failed.")
    return result.stdout
