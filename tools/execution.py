"""Execution and patch tools for a SWE-bench task workspace."""

import os
import subprocess
from pathlib import Path

from tools.filesystem import repository_root, resolve_task_path


def run_command(command: str, workdir: str = ".") -> dict:
    root = repository_root()
    cwd = resolve_task_path(workdir, root)
    if not cwd.is_dir():
        raise NotADirectoryError(workdir)
    timeout = float(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
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
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired as error:
        return {
            "stdout": error.stdout or "",
            "stderr": (error.stderr or "") + "\nCommand timed out.",
            "exit_code": 124,
        }


def run_tests() -> dict:
    script = os.getenv("EVAL_SCRIPT", "")
    script_path = os.getenv("EVAL_SCRIPT_PATH", "")
    if script_path:
        path = resolve_task_path(script_path)
        if not path.is_file():
            raise FileNotFoundError(script_path)
        script = path.read_text(encoding="utf-8")
    if not script:
        return {
            "stdout": "",
            "stderr": "No evaluation script configured (EVAL_SCRIPT or EVAL_SCRIPT_PATH).",
            "exit_code": 2,
        }
    result = subprocess.run(
        script,
        cwd=repository_root(),
        shell=True,
        executable="/bin/bash",
        capture_output=True,
        text=True,
        timeout=float(os.getenv("TEST_TIMEOUT_SECONDS", "600")),
        check=False,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


def get_patch() -> str:
    root = repository_root()
    result = subprocess.run(
        ["git", "-c", "core.fileMode=false", "diff"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git diff failed.")
    return result.stdout
