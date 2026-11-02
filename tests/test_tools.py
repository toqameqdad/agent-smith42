import subprocess

import pytest

from tools.code_search import (
    find_references,
    search_code,
    search_function_or_class_definition_in_code,
)
from tools.execution import get_patch, run_command
from tools.filesystem import edit_file, list_files, read_file, resolve_task_path


def test_filesystem_tools_are_scoped_and_line_numbered(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "sample.py"
    source.write_text("first\nsecond\n", encoding="utf-8")
    monkeypatch.setenv("TESTBED_PATH", str(root))

    assert read_file("sample.py", 2, 2) == "2: second"
    assert str(source) in list_files(".", "*.py")
    assert "Updated" in edit_file("sample.py", "second", "changed")
    assert source.read_text() == "first\nchanged\n"


def test_filesystem_rejects_path_traversal(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    monkeypatch.setenv("TESTBED_PATH", str(root))

    with pytest.raises(PermissionError):
        resolve_task_path("../secret.txt")


def test_edit_file_rejects_ambiguous_replacement(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("same same")
    monkeypatch.setenv("TESTBED_PATH", str(root))

    with pytest.raises(ValueError, match="occurs 2 times"):
        edit_file("a.txt", "same", "new")


def test_code_search_and_ast_definition_lookup(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "sample.py").write_text(
        "def target():\n    return 1\n\ntarget()\n"
    )
    monkeypatch.setenv("TESTBED_PATH", str(root))

    assert "sample.py:1" in search_code("def target", "*.py")
    assert "sample.py:1" in search_function_or_class_definition_in_code("target")
    assert "sample.py:4" in find_references("target")


def test_run_command_is_limited_to_repository(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("TESTBED_PATH", str(root))

    result = run_command("printf hello")
    assert result["stdout"] == "hello"
    assert result["exit_code"] == 0

    with pytest.raises(PermissionError):
        run_command("pwd", "../")


def test_get_patch_returns_git_diff(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@example.test"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Agent Smith"],
        cwd=root,
        check=True,
    )
    target = root / "file.txt"
    target.write_text("before\n")
    subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    target.write_text("after\n")
    monkeypatch.setenv("TESTBED_PATH", str(root))

    patch = get_patch()
    assert "--- a/file.txt" in patch
    assert "+after" in patch


def test_docker_workspace_sync_removes_stale_local_files(tmp_path, monkeypatch):
    from pathlib import Path
    from subprocess import CompletedProcess

    from tools.docker_workspace import sync_workspace_from_container

    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "stale.txt").write_text("stale")

    def fake_run(command, **kwargs):
        staging = Path(command[-1])
        (staging / "fresh.txt").write_text("fresh")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("tools.docker_workspace.subprocess.run", fake_run)
    sync_workspace_from_container("test-container", destination)

    assert not (destination / "stale.txt").exists()
    assert (destination / "fresh.txt").read_text() == "fresh"
