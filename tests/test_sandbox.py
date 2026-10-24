
from pathlib import Path
from sandbox.environment import get_python_paths, create_sandbox_directories, get_shared_libraries, prepare_library_mount_points, build_chroot_command, build_restricted_command
from sandbox.security import is_path_allowed, is_import_allowed, check_imports
from sandbox.executor import execute_code, send_to_worker
from sandbox.config import SandboxConfig
from sandbox.worker import is_runtime_ready
import sys


def test_allowed_import():
    """Verify that authorized modules are accepted."""
    allowed = ["math", "json", "collections.*"]

    assert is_import_allowed("math", allowed) is True
    assert is_import_allowed("json", allowed) is True
    assert is_import_allowed("collections.abc", allowed) is True


def test_blocked_import():
    """Verify that unauthorized modules are rejected."""
    allowed = ["math", "json", "collections.*"]

    assert is_import_allowed("os", allowed) is False
    assert is_import_allowed("subprocess", allowed) is False
    assert is_import_allowed("collections_fake", allowed) is False


def test_allowed_paths(tmp_path):
    """Verify that files inside an allowed directory are accepted."""

    allowed_dir = tmp_path / "testbed"
    allowed_dir.mkdir()

    existing_file = allowed_dir / "main.py"
    existing_file.write_text("print('Hello')")

    new_file = allowed_dir / "new_folder" / "solution.py"

    assert is_path_allowed(
        str(existing_file), [str(allowed_dir)], "read"
    ) is True

    assert is_path_allowed(
        str(new_file), [str(allowed_dir)], "create"
    ) is True


def test_blocked_paths(tmp_path):
    """Verify that paths outside allowed directories are rejected."""

    allowed_dir = tmp_path / "testbed"
    allowed_dir.mkdir()

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("Private data")

    traversal_path = allowed_dir / ".." / "secret.txt"

    assert is_path_allowed(
        str(outside_file), [str(allowed_dir)], "read"
    ) is False

    assert is_path_allowed(
        str(traversal_path), [str(allowed_dir)], "read"
    ) is False

    assert is_path_allowed(
        str(outside_file), [str(allowed_dir)], "create"
    ) is False


def test_symlink_escape(tmp_path):
    """Verify that symlinks cannot escape allowed directories."""

    allowed_dir = tmp_path / "testbed"
    allowed_dir.mkdir()

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("Private data")

    symlink = allowed_dir / "secret_link"
    symlink.symlink_to(outside_file)

    assert is_path_allowed(
        str(symlink),
        [str(allowed_dir)],
        "read"
    ) is False

    assert is_path_allowed(
        str(symlink),
        [str(allowed_dir)],
        "create"
    ) is False




def test_memory_limit():
    """Verify that excessive memory allocation is blocked."""

    config = SandboxConfig(
        max_execution_time_seconds=5,
        max_memory_mb=100
    )

    code = "data = bytearray(200 * 1024 * 1024)"

    result = execute_code(code, config)

    assert "MemoryError" in result




def test_execution_timeout():
    """Verify that execution stops when the timeout is exceeded."""

    config = SandboxConfig(
        max_execution_time_seconds=1,
        max_memory_mb=512
    )

    result = execute_code("while True: pass", config)

    assert "Execution timed out." in result


def test_check_imports():
    """Verify that AST import checks accept allowed modules and reject blocked ones."""

    allowed = ["math", "json", "collections.*"]

    assert check_imports("import math", allowed) is True
    assert check_imports("import os", allowed) is False

    assert check_imports(
        "from json import dumps", allowed
    ) is True

    assert check_imports(
        "import math, subprocess", allowed
    ) is False

    assert check_imports(
        "def test():\n    import os", allowed
    ) is False

    assert check_imports(
        "def broken(", allowed
    ) is False



def test_worker_communication():
    """Verify that the worker receives code and sandbox configuration."""

    config = SandboxConfig(
        max_execution_time_seconds=10,
        max_memory_mb=256
    )

    response = send_to_worker(
        "print(5 + 5)",
        config
    )

    assert response["status"] == "received"
    assert response["code"] == "print(5 + 5)"
    assert response["timeout"] == 10
    assert response["memory_mb"] == 256


def test_python_paths():
    """Verify that Python runtime paths are discovered correctly."""

    paths = get_python_paths()

    assert Path(paths["executable"]).is_file()
    assert Path(paths["base_prefix"]).is_dir()
    assert Path(paths["stdlib"]).is_dir()
    assert Path(paths["site_packages"]).is_dir()


def test_create_sandbox_directories(tmp_path):
    """Verify that the sandbox directory structure is created."""

    root = tmp_path / "sandbox-root"

    create_sandbox_directories(root)

    expected_directories = [
        "bin",
        "lib",
        "python",
        "app",
        "testbed",
        "tmp",
        "tmp/agent",
    ]

    for directory in expected_directories:
        assert (root / directory).is_dir()

def test_shared_libraries():
    """Verify that shared library dependencies are discovered."""

    libraries = get_shared_libraries(sys.executable)

    assert len(libraries) > 0

    for library in libraries:
        assert Path(library).is_file()


def test_prepare_library_mount_points(tmp_path):
    """Verify that library mount points are prepared correctly."""

    source = tmp_path / "source" / "libexample.so"
    source.parent.mkdir(parents=True)
    source.write_text("Example library")

    sandbox_root = tmp_path / "sandbox-root"

    mount_points = prepare_library_mount_points(
        sandbox_root,
        [str(source)],
    )

    assert len(mount_points) == 1

    actual_source, destination = mount_points[0]

    assert actual_source == source
    assert destination.is_file()
    assert destination.stat().st_size == 0
    assert destination == sandbox_root / source.relative_to("/")



def test_build_chroot_command(tmp_path):
    """Verify that the chroot command enables Python isolated mode."""

    root = tmp_path / "sandbox-root"
    code = 'print("Hello Agent Smith")'

    command = build_chroot_command(root, code)

    assert command == [
        "chroot",
        str(root),
        "/bin/python3.10",
        "-I",
        "-S",
        "-c",
        code,
    ]



def test_runtime_ready(tmp_path):
    """Verify that a prepared runtime passes the readiness check."""

    root = tmp_path / "sandbox-root"

    python_bin = root / "bin" / "python3.10"
    encodings = root / "install" / "lib" / "python3.10" / "encodings"

    python_bin.parent.mkdir(parents=True)
    python_bin.touch()

    encodings.mkdir(parents=True)

    assert is_runtime_ready(root) is True


def test_runtime_not_ready(tmp_path):
    """Verify that an incomplete runtime fails the readiness check."""

    root = tmp_path / "sandbox-root"

    assert is_runtime_ready(root) is False



def test_build_restricted_command():
    """Verify that privilege restrictions are added to a command."""

    original_command = ["python3", "--version"]

    restricted_command = build_restricted_command(original_command)

    assert restricted_command == [
        "setpriv",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "python3",
        "--version",
    ]

    assert original_command == ["python3", "--version"]
