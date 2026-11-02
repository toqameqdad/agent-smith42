"""Helpers for mirroring a SWE-bench Docker workspace to the host tools."""

import subprocess
from pathlib import Path


def docker_exec(
    container: str,
    arguments: list[str],
    timeout: float = 120,
    workdir: str = "/testbed",
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-w", workdir, container, *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def sync_workspace_from_container(container: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["docker", "cp", f"{container}:/testbed/.", str(destination)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Failed to sync workspace from Docker.")


def sync_file_to_container(container: str, root: Path, path: Path) -> None:
    relative = path.resolve().relative_to(root.resolve())
    target_directory = Path("/testbed") / relative.parent
    mkdir = docker_exec(
        container,
        ["mkdir", "-p", str(target_directory)],
        timeout=30,
    )
    if mkdir.returncode:
        raise RuntimeError(mkdir.stderr.strip() or "Failed to create remote directory.")
    result = subprocess.run(
        ["docker", "cp", str(path), f"{container}:{Path('/testbed') / relative}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Failed to sync edited file to Docker.")
