from pathlib import Path
from sandbox.environment import (
    get_python_paths,
    create_sandbox_directories,
    prepare_python_runtime,
    get_shared_libraries,
    prepare_library_mount_points,
    build_chroot_command,
    build_restricted_command,
)
from sandbox.security import (
    is_path_allowed,
    is_import_allowed,
    check_imports,
    apply_worker_seccomp_policy,
    create_safe_builtins,
)
from sandbox.executor import execute_code, send_to_worker
from sandbox.config import SandboxConfig
from sandbox.worker import is_runtime_ready, mount_runtime_libraries
import sys


def test_safe_builtins_allow_configured_imports():
    """Verify that restricted builtins allow imports from the allowlist."""
    namespace = {
        "__builtins__": create_safe_builtins(["math"], ["/tmp"]),
    }

    exec("import math", namespace)

    assert namespace["math"].sqrt(9) == 3


def test_safe_builtins_block_unconfigured_imports():
    """Verify that the restricted importer rejects modules outside the allowlist."""
    namespace = {
        "__builtins__": create_safe_builtins(["math"], ["/tmp"]),
    }

    try:
        exec("import os", namespace)
    except ImportError:
        pass
    else:
        raise AssertionError("Importing os should be blocked")


def test_safe_builtins_hide_dangerous_functions():
    """Verify that selected dangerous builtins are not exposed."""
    safe_builtins = create_safe_builtins(["math"], ["/tmp"])

    assert "open" in safe_builtins  # guarded by the directory allowlist
    assert "eval" not in safe_builtins
    assert "exec" not in safe_builtins
    assert "__import__" in safe_builtins



def test_guarded_open_allows_files_inside_allowed_directory(tmp_path):
    """Verify that guarded open permits access inside allowed directories."""
    allowed_dir = tmp_path / "testbed"
    allowed_dir.mkdir()
    target = allowed_dir / "result.txt"

    safe_builtins = create_safe_builtins(
        ["math"], [str(allowed_dir)]
    )
    namespace = {"__builtins__": safe_builtins}

    exec(
        "with open(" + repr(str(target)) + ", 'w') as file:\n"
        "    file.write('safe')",
        namespace,
    )

    assert target.read_text() == "safe"


def test_guarded_open_blocks_files_outside_allowed_directory(tmp_path):
    """Verify that guarded open rejects paths outside the allowlist."""
    allowed_dir = tmp_path / "testbed"
    allowed_dir.mkdir()
    outside_file = tmp_path / "secret.txt"

    safe_builtins = create_safe_builtins(
        ["math"], [str(allowed_dir)]
    )
    namespace = {"__builtins__": safe_builtins}

    try:
        exec(
            "open(" + repr(str(outside_file)) + ", 'w')",
            namespace,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Opening an outside path should be blocked")


def test_safe_builtins_support_class_definitions():
    """Verify that restricted builtins retain Python class support."""
    namespace = {
        "__name__": "__main__",
        "__builtins__": create_safe_builtins(["math"], ["/tmp"]),
    }

    exec("""class Example:
    pass""", namespace)
    assert namespace["Example"]().__class__.__name__ == "Example"


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
    """Verify worker execution when Linux namespaces are available."""
    import subprocess
    import pytest

    try:
        namespace_probe = subprocess.run(
            [
                "unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--net",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"Linux namespaces are unavailable: {error}")

    if namespace_probe.returncode != 0:
        pytest.skip(
            "Linux user, mount, and network namespaces are unavailable: "
            + namespace_probe.stderr.strip()
        )

    config = SandboxConfig(
        max_execution_time_seconds=10,
        max_memory_mb=256
    )

    response = send_to_worker(
        "print(5 + 5)",
        config
    )

    assert response["status"] == "success", response
    assert response["output"] == "10\n"


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
        "-B",
        "-c",
        code,
    ]


def test_build_chroot_command_with_bootstrap(tmp_path):
    """Verify that the chroot command runs the trusted bootstrap."""

    root = tmp_path / "sandbox-root"
    bootstrap = "print('Trusted bootstrap')"

    command = build_chroot_command(root, bootstrap)

    assert command[-2:] == ["-c", bootstrap]
    assert command[0] == "chroot"
    assert command[1] == str(root)


def test_mount_runtime_libraries_sets_read_only_root_and_writable_allowlist(
    tmp_path, monkeypatch
):
    """Verify mount order leaves only configured directories writable."""
    import sandbox.worker as worker

    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python3.10").touch()
    (root / "testbed").mkdir()
    (root / "tmp" / "agent").mkdir(parents=True)

    library = tmp_path / "libexample.so"
    library.touch()

    commands = []
    monkeypatch.setattr(
        worker, "get_shared_libraries", lambda _executable: [str(library)]
    )
    monkeypatch.setattr(
        worker,
        "_run_mount_command",
        lambda arguments: commands.append(arguments),
    )

    mount_runtime_libraries(root, ["/testbed", "/tmp/agent"])

    assert ["--bind", str(root.resolve()), str(root.resolve())] in commands
    assert [
        "-o", "remount,bind,ro",
        str(root.resolve() / Path(str(library).lstrip("/"))),
    ] in commands
    assert [
        "-o", "remount,bind,ro", str(root.resolve())
    ] in commands
    assert [
        "-o", "remount,bind,rw", str(root.resolve() / "testbed")
    ] in commands
    assert [
        "-o", "remount,bind,rw", str(root.resolve() / "tmp" / "agent")
    ] in commands


def test_mount_runtime_libraries_rejects_paths_outside_root(
    tmp_path, monkeypatch
):
    """Reject configured writable paths that escape the chroot root."""
    import sandbox.worker as worker

    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python3.10").touch()
    (root / "testbed").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.setattr(worker, "get_shared_libraries", lambda _exe: [])
    monkeypatch.setattr(worker, "_run_mount_command", lambda _args: None)

    try:
        mount_runtime_libraries(root, [str(outside)])
    except ValueError:
        pass
    else:
        raise AssertionError("Outside writable directory should be rejected")


def test_runtime_ready(tmp_path):
    """Verify that a prepared runtime passes the readiness check."""

    root = tmp_path / "sandbox-root"

    python_bin = root / "bin" / "python3.10"
    encodings = root / "lib" / "python3.10" / "encodings"
    lib_dynload = root / "lib" / "python3.10" / "lib-dynload"

    python_bin.parent.mkdir(parents=True)
    python_bin.touch()

    encodings.mkdir(parents=True)
    lib_dynload.mkdir(parents=True)

    (root / "install").symlink_to(".")

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



def test_seccomp_filter():
    """Verify that Seccomp blocks a syscall in a child process."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import ctypes
        import errno

        from sandbox.security import apply_seccomp_filter

        libseccomp = ctypes.CDLL("libseccomp.so.2")
        libseccomp.seccomp_syscall_resolve_name.argtypes = [
            ctypes.c_char_p
        ]
        libseccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int

        syscall_number = libseccomp.seccomp_syscall_resolve_name(
            b"getppid"
        )

        assert syscall_number >= 0

        apply_seccomp_filter(["getppid"])

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.argtypes = [ctypes.c_long]
        libc.syscall.restype = ctypes.c_long

        ctypes.set_errno(0)
        result = libc.syscall(syscall_number)
        error = ctypes.get_errno()

        print("Result:", result)
        print("Errno:", error)

        assert result == -1
        assert error == errno.EPERM

        print("Seccomp test passed")
    """)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "Seccomp test passed" in result.stdout


def test_worker_seccomp_policy():
    """Verify that the worker policy blocks namespace creation."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import ctypes
        import errno

        from sandbox.security import apply_worker_seccomp_policy

        libseccomp = ctypes.CDLL("libseccomp.so.2")
        libseccomp.seccomp_syscall_resolve_name.argtypes = [
            ctypes.c_char_p
        ]
        libseccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int

        syscall_number = libseccomp.seccomp_syscall_resolve_name(
            b"unshare"
        )
        assert syscall_number >= 0

        apply_worker_seccomp_policy()

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long

        ctypes.set_errno(0)
        result = libc.syscall(syscall_number, 0)
        error = ctypes.get_errno()

        assert result == -1
        assert error == errno.EPERM

        print("Worker Seccomp policy test passed")
    """)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "Worker Seccomp policy test passed" in result.stdout


def test_seccomp_denial_is_effective():
    """Verify that Seccomp changes an allowed syscall into a denied one."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import ctypes
        import errno

        from sandbox.security import apply_seccomp_filter

        libseccomp = ctypes.CDLL("libseccomp.so.2")

        libseccomp.seccomp_syscall_resolve_name.argtypes = [
            ctypes.c_char_p
        ]
        libseccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int

        syscall_number = libseccomp.seccomp_syscall_resolve_name(
            b"getppid"
        )
        assert syscall_number >= 0

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.argtypes = [ctypes.c_long]
        libc.syscall.restype = ctypes.c_long

        # Before the filter, the syscall should succeed.
        ctypes.set_errno(0)
        before = libc.syscall(syscall_number)
        assert before > 0

        # Install the filter in this child process only.
        apply_seccomp_filter(["getppid"])

        ctypes.set_errno(0)
        after = libc.syscall(syscall_number)
        error = ctypes.get_errno()

        assert after == -1
        assert error == errno.EPERM

        print("Seccomp denial verified")
    """)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Seccomp denial verified" in result.stdout



def test_clone_namespace_seccomp_rule():
    """Verify that Seccomp blocks clone calls requesting namespaces."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import ctypes
        import errno

        from sandbox.security import apply_worker_seccomp_policy

        libseccomp = ctypes.CDLL("libseccomp.so.2")
        libseccomp.seccomp_syscall_resolve_name.argtypes = [
            ctypes.c_char_p
        ]
        libseccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int

        clone_number = libseccomp.seccomp_syscall_resolve_name(
            b"clone"
        )
        assert clone_number >= 0

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long

        apply_worker_seccomp_policy()

        # CLONE_NEWUSER | SIGCHLD
        flags = 0x10000000 | 17

        ctypes.set_errno(0)
        result = libc.syscall(clone_number, flags, 0, 0, 0, 0)
        error = ctypes.get_errno()

        assert result == -1
        assert error == errno.EPERM

        print("Clone namespace rule passed")
    """)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "Clone namespace rule passed" in result.stdout



def test_seccomp_allows_normal_threads():
    """Verify that normal Python threads work after applying Seccomp."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import threading

        from sandbox.security import apply_worker_seccomp_policy

        # Install the experimental worker filter.
        apply_worker_seccomp_policy()

        results = []

        def worker():
            results.append(42)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert results == [42]

        print("Normal threads work")
    """)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "Normal threads work" in result.stdout


def test_check_imports_multiple_allowed():
    allowed = ["math", "json"]

    assert check_imports(
        "import math, json",
        allowed
    ) is True


def test_check_imports_from_allowed_submodule():
    allowed = ["collections.*"]

    assert check_imports(
        "from collections.abc import Sequence",
        allowed
    ) is True


def test_check_imports_relative_import():
    allowed = ["math", "json"]

    assert check_imports(
        "from .helpers import something",
        allowed
    ) is False


def test_check_imports_dynamic_import():
    allowed = ["math"]

    assert check_imports(
        "__import__('os')",
        allowed
    ) is False


def test_check_imports_importlib():
    allowed = ["math"]

    assert check_imports(
        "import importlib",
        allowed
    ) is False


def test_check_imports_builtins_import():
    allowed = ["math"]

    assert check_imports(
        'import builtins\nbuiltins.__import__("os")',
        allowed
    ) is False


def test_check_imports_getattr():
    allowed = ["math"]

    assert check_imports(
        'getattr(__builtins__, "__import__")("os")',
        allowed
    ) is False


def test_prepare_python_runtime(tmp_path):
    """Verify that Python runtime files are copied into the sandbox."""

    root = tmp_path / "sandbox-root"

    prepare_python_runtime(root)

    assert (root / "bin" / "python3.10").is_file()
    assert (root / "lib" / "python3.10" / "encodings").is_dir()
    assert (root / "lib" / "python3.10" / "lib-dynload").is_dir()
    assert not (
        root / "lib" / "python3.10" / "site-packages"
    ).exists()


def test_set_no_new_privs():
    import ctypes

    from sandbox.security import set_no_new_privs

    set_no_new_privs()

    libc = ctypes.CDLL(None, use_errno=True)

    PR_GET_NO_NEW_PRIVS = 39

    result = libc.prctl(
        PR_GET_NO_NEW_PRIVS,
        0,
        0,
        0,
        0,
    )

    assert result == 1


def test_seccomp_bootstrap_structure():
    from sandbox.worker import build_bootstrap_code

    bootstrap = build_bootstrap_code()

    assert "sys.stdin.read()" in bootstrap
    assert "set_no_new_privs" in bootstrap
    assert "seccomp_load" in bootstrap
    assert "apply_landlock" in bootstrap
    assert "exec(" in bootstrap
    assert "final_answer" in bootstrap
    assert "__AGENT_SMITH_FINAL_ANSWER__" in bootstrap



def test_landlock_restricts_filesystem_access(tmp_path):
    """Verify Landlock enforces the filesystem allowlist."""
    import subprocess
    import textwrap

    allowed_dir = tmp_path / "allowed"
    outside_dir = tmp_path / "outside"
    allowed_dir.mkdir()
    outside_dir.mkdir()

    allowed_file = allowed_dir / "read.txt"
    outside_file = outside_dir / "secret.txt"
    allowed_file.write_text("allowed")
    outside_file.write_text("secret")

    code = textwrap.dedent("""
        import sys
        from sandbox.security import apply_landlock

        allowed_dir, outside_dir = sys.argv[1:3]
        apply_landlock([allowed_dir], [])

        with open(allowed_dir + "/read.txt") as file:
            assert file.read() == "allowed"

        with open(allowed_dir + "/created.txt", "w") as file:
            file.write("created")

        try:
            open(outside_dir + "/secret.txt").read()
        except PermissionError:
            pass
        else:
            raise AssertionError("Landlock allowed reading outside the allowlist")

        try:
            open(outside_dir + "/created.txt", "w")
        except PermissionError:
            pass
        else:
            raise AssertionError("Landlock allowed writing outside the allowlist")

        print("Landlock filesystem restrictions passed")
    """)

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            code,
            str(allowed_dir),
            str(outside_dir),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "Landlock filesystem restrictions passed" in result.stdout
    assert (allowed_dir / "created.txt").read_text() == "created"
    assert not (outside_dir / "created.txt").exists()



def test_worker_seccomp_blocks_network_syscalls():
    """Verify the worker policy blocks direct network socket creation."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent("""
        import ctypes
        import errno

        from sandbox.security import apply_worker_seccomp_policy

        libseccomp = ctypes.CDLL("libseccomp.so.2")
        libseccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        libseccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int

        socket_number = libseccomp.seccomp_syscall_resolve_name(b"socket")
        assert socket_number >= 0

        apply_worker_seccomp_policy()

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long

        ctypes.set_errno(0)
        result = libc.syscall(socket_number, 2, 1, 0)
        error = ctypes.get_errno()

        assert result == -1
        assert error == errno.EPERM
        print("Network syscall restriction passed")
    """)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"STDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}"
    )
    assert "Network syscall restriction passed" in result.stdout



def test_interactive_bootstrap_code_is_valid_and_injects_final_answer():
    """Verify the persistent bootstrap keeps state and exposes final_answer."""
    from sandbox.worker import build_interactive_bootstrap_code

    source = build_interactive_bootstrap_code(
        ["math"],
        ["/testbed", "/tmp/agent"],
    )

    compile(source, "<interactive-bootstrap-test>", "exec")
    assert "user_namespace['final_answer'] = final_answer" in source
    assert "for line in sys.stdin" in source
    assert "FinalAnswerSignal" in source



def test_interactive_bootstrap_exposes_dynamic_mcp_tools():
    """Verify MCP tool names and schemas are injected into the sandbox."""
    from sandbox.worker import build_interactive_bootstrap_code

    source = build_interactive_bootstrap_code(
        ["json"],
        ["/testbed", "/tmp/agent"],
        [
            {
                "name": "search_docs",
                "description": "Search project documentation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ],
        "Dynamic MCP manual",
    )

    compile(source, "<mcp-bootstrap-test>", "exec")
    assert "search_docs" in source
    assert "Dynamic MCP manual" in source
    assert "mcp_read_resource" in source
    assert "mcp_get_prompt" in source


def test_mcp_manual_uses_connected_server_schemas():
    """Verify the MCP manual is generated from live tool/resource/prompt data."""
    from sandbox.mcp_client import MCPClient

    client = MCPClient.__new__(MCPClient)
    client.tools = [
        {
            "name": "search_docs",
            "description": "Search documentation.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
    ]
    client.resources = [{"name": "project files", "uri": "file:///project"}]
    client.prompts = [{"name": "review", "description": "Review a change."}]

    manual = client.build_manual()

    assert "search_docs" in manual
    assert '"query"' in manual
    assert "mcp_read_resource(uri)" in manual
    assert "mcp_get_prompt(name, arguments={})" in manual
    assert "file:///project" in manual
    assert "review" in manual



def test_mcp_sdk_client_imports():
    """Verify the installed MCP SDK exposes both supported client transports."""
    from mcp import Client, StdioServerParameters

    assert Client is not None
    assert StdioServerParameters is not None



def test_mcp_discovery_reads_all_pages():
    """Verify MCP discovery follows next-cursor pagination."""
    import asyncio
    from types import SimpleNamespace
    from sandbox.mcp_client import MCPClient

    class FakeMCP:
        async def list_tools(self, cursor=None):
            if cursor is None:
                return SimpleNamespace(
                    tools=[{"name": "first"}],
                    nextCursor="page-2",
                )
            return SimpleNamespace(
                tools=[{"name": "second"}],
                nextCursor=None,
            )

    client = MCPClient.__new__(MCPClient)
    client._client = FakeMCP()

    tools = asyncio.run(client._safe_list("list_tools", "tools"))

    assert [tool["name"] for tool in tools] == ["first", "second"]



def test_one_shot_final_answer_is_captured():
    """Verify final_answer stops execution and emits the worker protocol marker."""
    import subprocess

    security_source = (
        "def apply_worker_seccomp_policy(): pass\n"
        "def apply_landlock(*args): pass\n"
        "def create_safe_builtins(*args): return __builtins__\n"
    )
    user_code = 'print("before"); final_answer("finished"); print("after")'
    code = (
        "from sandbox.bootstrap import run\n"
        f"security_source = {security_source!r}\n"
        f"run({user_code!r}, security_source, [], [])\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "before" in result.stdout
    assert '__AGENT_SMITH_FINAL_ANSWER__:"finished"' in result.stdout
    assert "after" not in result.stdout


def test_repl_stops_after_final_answer(monkeypatch):
    """Verify final_answer ends the interactive task instead of reading more input."""
    import builtins
    from types import SimpleNamespace
    from sandbox.__main__ import run_repl

    class FakeSession:
        def __init__(self, *args, **kwargs):
            self.process = SimpleNamespace(poll=lambda: None)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, code):
            assert code == "final_answer('finished')"
            return {
                "status": "final_answer",
                "answer": "finished",
                "output": "",
            }

    inputs = iter(["final_answer('finished')", "this must not be read"])
    monkeypatch.setattr("sandbox.__main__.SandboxSession", FakeSession)
    monkeypatch.setattr(builtins, "input", lambda prompt: next(inputs))

    assert run_repl(SandboxConfig()) == 0
    assert next(inputs) == "this must not be read"


def test_mcp_discovery_does_not_hide_transport_errors():
    """Verify MCP discovery errors are reported instead of becoming empty lists."""
    import asyncio
    from sandbox.mcp_client import MCPClient

    class BrokenMCP:
        async def list_tools(self, cursor=None):
            raise RuntimeError("MCP transport failed")

    client = MCPClient.__new__(MCPClient)
    client._client = BrokenMCP()

    try:
        asyncio.run(client._safe_list("list_tools", "tools"))
    except RuntimeError as error:
        assert str(error) == "MCP transport failed"
    else:
        raise AssertionError("MCP discovery silently hid a transport failure")


def test_mcp_stdio_client_end_to_end(tmp_path):
    """Verify stdio discovery and tool/resource/prompt calls against a real MCP server."""
    import sys
    from sandbox.mcp_client import MCPClient

    server_file = tmp_path / "mcp_server.py"
    server_file.write_text(
        "from pydantic import BaseModel\n"
        "from mcp.server import MCPServer\n"
        "class Sum(BaseModel):\n"
        "    result: int\n"
        "mcp = MCPServer('Agent Smith test server')\n"
        "@mcp.tool()\n"
        "def add(a: int, b: int) -> Sum:\n"
        "    return Sum(result=a + b)\n"
        "@mcp.resource('demo://status')\n"
        "def status() -> str:\n"
        "    return 'ready'\n"
        "@mcp.prompt()\n"
        "def review(topic: str) -> str:\n"
        "    return f'Review {topic}'\n"
        "if __name__ == '__main__':\n"
        "    mcp.run()\n",
        encoding="utf-8",
    )

    with MCPClient(
        stdio_command=f'"{sys.executable}" "{server_file}"'
    ) as client:
        assert [tool["name"] for tool in client.tools] == ["add"]
        assert any(item.get("uri") == "demo://status" for item in client.resources)
        assert any(item.get("name") == "review" for item in client.prompts)

        tool_result = client.handle_call(
            "call_tool",
            {"name": "add", "arguments": {"a": 2, "b": 3}},
        )
        assert tool_result["structuredContent"]["result"] == 5

        resource_result = client.handle_call(
            "read_resource",
            {"uri": "demo://status"},
        )
        assert resource_result["contents"][0]["text"] == "ready"

        prompt_result = client.handle_call(
            "get_prompt",
            {"name": "review", "arguments": {"topic": "sandbox"}},
        )
        assert "Review sandbox" in prompt_result["messages"][0]["content"]["text"]
