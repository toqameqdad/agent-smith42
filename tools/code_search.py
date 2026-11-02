"""Repository-wide code search tools."""

import ast
import re
from pathlib import Path

from tools.filesystem import repository_root, resolve_task_path


IGNORED = {".git", "__pycache__", ".venv", "node_modules"}


def _source_files(root: Path, file_pattern: str = "*"):
    for path in root.rglob(file_pattern):
        if path.is_file() and not any(part in IGNORED for part in path.parts):
            yield path


def search_code(pattern: str, file_pattern: str = "*") -> str:
    root = repository_root()
    try:
        expression = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"Invalid search pattern: {error}") from error
    results = []
    for path in _source_files(root, file_pattern):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, content in enumerate(lines, 1):
            if expression.search(content):
                results.append(f"{path.resolve()}:{number} {content}")
    return "\n".join(results) if results else "No matches found."


def search_function_or_class_definition_in_code(name: str) -> str:
    root = repository_root()
    results = []
    for path in _source_files(root, "*.py"):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == name:
                    line = source.splitlines()[node.lineno - 1]
                    results.append(f"{path.resolve()}:{node.lineno} {line}")
    return "\n".join(results) if results else "Definition not found."


def find_references(name: str, filepath: str = "", line: int = 0) -> str:
    root = repository_root()
    if filepath:
        resolve_task_path(filepath, root)
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    results = []
    for path in _source_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, content in enumerate(lines, 1):
            if pattern.search(content):
                results.append(f"{path.resolve()}:{number} {content}")
    return "\n".join(results) if results else "No references found."
