
import sys
import sysconfig
from pathlib import Path
import subprocess


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
        "site_packages": str(
			Path(sysconfig.get_path("purelib")).resolve()
		),
    }

def create_sandbox_directories(root: Path) -> None:
	"""
    Create the basic directory structure for a sandbox root.

    Args:
        root (Path): Root directory of the sandbox environment.

    Returns:
        None: Creates directories without returning a value.
    """
	directories = [
		"bin",
		"lib",
		"python",
		"app",
		"testbed",
		"tmp/agent",
	]

	for directory in directories:
		(root / directory).mkdir(parents=True, exist_ok=True)

def get_shared_libraries(executable: str) -> list[str]:
	"""
    Discover shared library paths required by an executable.

    Args:
        executable (str): Path to a trusted executable.

    Returns:
        list[str]: Absolute paths to shared libraries.
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

	return libraries

def prepare_library_mount_points(
	root: Path,
	libraries: list[str],
) ->list[tuple[Path, Path]]:
	"""
    Prepare mount points for shared libraries inside a sandbox.

    Args:
        root (Path): Sandbox root directory.
        libraries (list[str]): Absolute paths to trusted libraries.

    Returns:
        list[tuple[Path, Path]]: Source and destination path pairs.
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

    Args:
        root (Path): Path to the prepared sandbox root.
        code (str): Trusted Python code to execute.

    Returns:
        list[str]: Command arguments for chroot.
    """
	return [
		"chroot",
		str(root),
		"/bin/python3.10",
		"-I",
		"-S",
		"-c",
		code,
	]

def build_restricted_command(command: list[str]) -> list[str]:
	"""
    Wrap a trusted command with Linux privilege restrictions.

    Args:
        command (list[str]): Trusted command and its arguments.

    Returns:
        list[str]: Command prefixed with privilege restrictions.
    """
	return [
		"setpriv",
		"--no-new-privs",
		"--bounding-set=-all",
		"--inh-caps=-all",
		"--ambient-caps=-all",
		*command,
	]