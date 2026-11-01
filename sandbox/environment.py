
import sys
import sysconfig
import subprocess
import shutil
from pathlib import Path


def get_python_paths() -> dict[str, str]:
    """
    Discover Python runtime paths for sandbox preparation.

    Returns:
        dict[str, str]: Python executable, installation,
        standard library, and virtual environment paths.
    """
    return {
        "executable": str(Path(sys.executable).resolve()),
        "base_prefix": str(Path(sys.base_prefix).resolve()),
        "stdlib": str(Path(sysconfig.get_path("stdlib")).resolve()),
        "site_packages": str(Path(sysconfig.get_path("purelib")).resolve()),
    }


def create_sandbox_directories(root: str | Path) -> Path:
    root = Path(root)

    directories = [
        "bin",
        "lib",
        "python",
        "app",
        "testbed",
        "tmp/agent",
    ]

    root.mkdir(parents=True, exist_ok=True)

    for directory in directories:
        (root / directory).mkdir(parents=True, exist_ok=True)

    return root


def prepare_python_runtime(root: str | Path) -> Path:
    """
    Prepare an isolated Python runtime inside the sandbox.

    Args:
        root: Path to the sandbox root.

    Returns:
        Path: The prepared sandbox root.
    """
    root = create_sandbox_directories(root)
    paths = get_python_paths()

    base_prefix = Path(paths["base_prefix"])
    stdlib = Path(paths["stdlib"])

    python_source = base_prefix / "bin" / "python3.10"
    python_destination = root / "bin" / "python3.10"

    library_destination = root / "lib" / "python3.10"

    if not python_source.is_file():
        raise FileNotFoundError(
            f"Python executable not found: {python_source}"
        )

    if not stdlib.is_dir():
        raise FileNotFoundError(
            f"Python standard library not found: {stdlib}"
        )

    shutil.copy2(
        python_source,
        python_destination,
        follow_symlinks=False,
    )

    shutil.copytree(
        stdlib,
        library_destination,
        symlinks=True,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "site-packages",
            "__pycache__",
        ),
    )

    install_prefix = root / "install"

    if install_prefix.is_symlink():
        if install_prefix.resolve() != root.resolve():
            install_prefix.unlink()
            install_prefix.symlink_to(".")
    elif install_prefix.exists():
        raise FileExistsError(
            f"Cannot create Python prefix symlink: {install_prefix}"
        )
    else:
        install_prefix.symlink_to(".")
    return root

    
def get_shared_libraries(executable: str) -> list[str]:
    """
    Discover shared library paths required by an executable.
    """
    result = subprocess.run(
        ["ldd", executable],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    libraries = []

    for line in result.stdout.splitlines():
        parts = line.strip().split()

        if "=>" in parts:
            index = parts.index("=>")
            path = parts[index + 1]

            if path.startswith("/"):
                libraries.append(path)

        elif parts and parts[0].startswith("/"):
            libraries.append(parts[0])

    # Python builds configured with --enable-shared may need libpython
    # at runtime even when ldd does not report it as a resolved dependency.
    library_name = sysconfig.get_config_var("LDLIBRARY")
    library_directory = sysconfig.get_config_var("LIBDIR")
    if library_name and library_directory:
        python_library = Path(library_directory) / library_name
        if python_library.is_file():
            libraries.append(str(python_library))

    return libraries


def prepare_library_mount_points(
    root: Path,
    libraries: list[str],
) -> list[tuple[Path, Path]]:
    """
    Prepare mount points for shared libraries inside a sandbox.
    """
    mount_points = []

    for library in libraries:
        source = Path(library)

        if not source.is_absolute() or not source.is_file():
            raise ValueError(f"Invalid library path: {library}")

        destination = root / source.relative_to("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch(exist_ok=True)

        mount_points.append((source, destination))

    return mount_points


def build_chroot_command(
    root: Path,
    code: str,
) -> list[str]:
    """
    Build a command for running Python inside a chroot.
    """
    return [
        "chroot",
        str(root),
        "/bin/python3.10",
        "-I",
        "-S",
        "-B",
        "-c",
        code,
    ]


def build_restricted_command(command: list[str]) -> list[str]:
    """
    Wrap a trusted command with Linux privilege restrictions.
    """
    return [
        "setpriv",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        *command,
    ]
