"""Filesystem tools scoped to the SWE-bench task repository."""

import fnmatch
import os
from pathlib import Path

from tools.docker_workspace import sync_file_to_container


def repository_root() -> Path:
    return Path(os.getenv("TESTBED_PATH", os.getcwd())).resolve()


def resolve_task_path(path: str, root: Path | None = None) -> Path:
    root = root or repository_root()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError(f"Path is outside the task repository: {path}")
    return resolved


def read_file(
    filepath: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    path = resolve_task_path(filepath)
    if not path.is_file():
        raise FileNotFoundError(filepath)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line or 1)
    end = min(len(lines), end_line or len(lines))
    if end < start:
        return ""
    return "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(start, end + 1)
    )


def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    path = resolve_task_path(filepath)
    if not path.is_file():
        raise FileNotFoundError(filepath)
    content = path.read_text(encoding="utf-8")
    occurrences = content.count(old_str)
    if occurrences == 0:
        raise ValueError("The exact old_str was not found; file was not changed.")
    if occurrences > 1:
        raise ValueError(
            f"old_str occurs {occurrences} times; provide a unique exact match."
        )
    path.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")
    container = os.getenv("DOCKER_CONTAINER")
    if container:
        sync_file_to_container(container, repository_root(), path)
    return f"Updated {path}"


def list_files(directory: str = ".", pattern: str = "*") -> str:
    root = repository_root()
    target = resolve_task_path(directory, root)
    if not target.is_dir():
        raise NotADirectoryError(directory)
    matches = sorted(
        str(path)
        for path in target.rglob("*")
        if path.is_file()
        and fnmatch.fnmatch(path.name, pattern)
        and not any(part in {".git", "__pycache__", ".venv"} for part in path.parts)
    )
    return "\n".join(matches) if matches else "No matching files."
