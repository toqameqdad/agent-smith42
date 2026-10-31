from pathlib import Path
import ast
import builtins
import ctypes
import errno


BLOCKED_WORKER_SYSCALLS = [
    "mount",
    "umount2",
    "pivot_root",
    "chroot",
    "unshare",
    "setns",
    "setuid",
    "setgid",
    "setresuid",
    "setresgid",
    "setgroups",
    "ptrace",
    "bpf",
]


class ScmpArgCmp(ctypes.Structure):
    """Represent a syscall argument comparison for libseccomp."""

    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def add_clone_namespace_rules(lib, context: int) -> None:
    """
    Block clone calls that request new Linux namespaces.

    Args:
        lib: Loaded libseccomp library.
        context (int): Active Seccomp filter context.

    Raises:
        RuntimeError: If a Seccomp rule cannot be added.
    """
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(ScmpArgCmp),
    ]
    lib.seccomp_rule_add_array.restype = ctypes.c_int

    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int

    clone_number = lib.seccomp_syscall_resolve_name(b"clone")

    if clone_number < 0:
        raise RuntimeError("Cannot resolve clone syscall")

    SCMP_CMP_MASKED_EQ = 7
    deny = 0x00050000 | errno.EPERM

    for flag in get_namespace_clone_flag_values():
        comparison = ScmpArgCmp(
            arg=0,
            op=SCMP_CMP_MASKED_EQ,
            datum_a=flag,
            datum_b=flag,
        )

        result = lib.seccomp_rule_add_array(
            context,
            deny,
            clone_number,
            1,
            ctypes.byref(comparison),
        )

        if result != 0:
            raise RuntimeError(
                f"Failed to block clone flag {hex(flag)}: {result}"
            )


def create_safe_builtins() -> dict:
    """
    Create a limited dictionary of Python built-in functions.

    Returns:
        dict: Selected built-in functions for controlled execution.
    """
    allowed_names = [
        "print",
        "len",
        "range",
        "sum",
        "min",
        "max",
        "abs",
        "enumerate",
        "zip",
    ]

    return {
        name: getattr(builtins, name)
        for name in allowed_names
    }

def is_import_allowed(
    module_name: str,
    authorized_imports: list[str],
    ) -> bool:
    """
    Check if the import of a module is allowed based on a predefined list of allowed modules.

    Args:
        module_name (str): The name of the module to check.
        authorized_imports (list[str]): A list of authorized module names or patterns.

    """
    for allowed in authorized_imports:
        if module_name == allowed:
            return True

        if allowed.endswith(".*"):
            parent = allowed[:-2]

            if module_name.startswith(parent + "."):
                return True
            
    return False


def is_path_allowed(
    file_path: str,
    allowed_directories: list[str],
    operation: str = "read"
    ) -> bool:
    """
    Check if the file path is allowed based on a predefined list of allowed directories.

    Args:
        file_path (str): The file path to check.
        allowed_directories (list[str]): A list of allowed directory paths.
        operation (str): The operation being performed (read or create).
    """
    if operation not in ["read", "create"]:
        return False

    try:
        resolved_path = Path(file_path).resolve(
            strict=(operation == "read")
        )
    except (OSError, RuntimeError):
        return False

    for directory in allowed_directories:
        try:
            resolve_dir = Path(directory).resolve(strict=True)

            if not resolve_dir.is_dir():
                continue

            if resolved_path.is_relative_to(resolve_dir):
                return True

        except (OSError, RuntimeError):
            continue

    return False

def check_imports(
    code: str,
    authorized_imports: list[str]
) -> bool:
    """
    Check whether imports in Python code are allowed.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):
            for alias in node.names:
                if not is_import_allowed(
                    alias.name,
                    authorized_imports
                ):
                    return False

        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""

            if node.level != 0:
                return False

            if not is_import_allowed(
                module_name,
                authorized_imports
            ):
                return False

        elif isinstance(node, ast.Call):

            # Block __import__()
            if isinstance(node.func, ast.Name):
                if node.func.id == "__import__":
                    return False

            # Block importlib.import_module()
            if isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr == "import_module"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                ):
                    return False

    return True



def apply_seccomp_filter(
    blocked_syscalls: list[str],
    block_namespace_clone: bool = False,
    enosys_syscalls: list[str] | None = None,
    ) -> None:
    """
    Apply a Seccomp filter that denies selected Linux system calls.

    Args:
        blocked_syscalls (list[str]): System call names to deny.

    Raises:
        RuntimeError: If filter creation or installation fails.
    """
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)

    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p

    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int

    lib.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    lib.seccomp_rule_add.restype = ctypes.c_int

    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int

    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None

    allow = 0x7FFF0000
    deny = 0x00050000 | errno.EPERM

    context = lib.seccomp_init(allow)
    if not context:
        raise RuntimeError("Failed to initialize Seccomp")

    try:
        for name in blocked_syscalls:
            syscall = lib.seccomp_syscall_resolve_name(
                name.encode("ascii")
            )

            if syscall < 0:
                raise RuntimeError(f"Unknown syscall: {name}")

            result = lib.seccomp_rule_add(
                context, deny, syscall, 0
            )

            if result != 0:
                raise RuntimeError(
                    f"Failed to block {name}: {result}"
                )

        for name in enosys_syscalls or []:
            syscall = lib.seccomp_syscall_resolve_name(
                name.encode("ascii")
            )

            if syscall < 0:
                raise RuntimeError(f"Unknown syscall: {name}")

            result = lib.seccomp_rule_add(
                context,
                0x00050000 | errno.ENOSYS,
                syscall,
                0,
            )

            if result != 0:
                raise RuntimeError(
                    f"Failed to disable {name}: {result}"
                )

        if block_namespace_clone:
            add_clone_namespace_rules(lib, context)
        result = lib.seccomp_load(context)

        if result != 0:
            raise RuntimeError(
                f"Failed to install Seccomp: {result}"
            )

    finally:
        lib.seccomp_release(context)



def get_namespace_clone_flags() -> int:
    """
    Return Linux clone flags used to create new namespaces.

    Returns:
        int: Bitmask of namespace creation flags.
    """
    clone_newns = 0x00020000
    clone_newcgroup = 0x02000000
    clone_newuts = 0x04000000
    clone_newipc = 0x08000000
    clone_newuser = 0x10000000
    clone_newpid = 0x20000000
    clone_newnet = 0x40000000

    return (
        clone_newns
        | clone_newcgroup
        | clone_newuts
        | clone_newipc
        | clone_newuser
        | clone_newpid
        | clone_newnet
    )


def get_namespace_clone_flag_values() -> list[int]:
    """
    Return individual Linux namespace creation flags for clone.

    Returns:
        list[int]: Individual namespace creation flag values.
    """
    return [
        0x00020000,  # CLONE_NEWNS
        0x02000000,  # CLONE_NEWCGROUP
        0x04000000,  # CLONE_NEWUTS
        0x08000000,  # CLONE_NEWIPC
        0x10000000,  # CLONE_NEWUSER
        0x20000000,  # CLONE_NEWPID
        0x40000000,  # CLONE_NEWNET
    ]



def apply_worker_seccomp_policy() -> None:
    """
    Apply the experimental Seccomp policy for the sandbox worker.
    """
    apply_seccomp_filter(
        blocked_syscalls=BLOCKED_WORKER_SYSCALLS,
        block_namespace_clone=True,
        enosys_syscalls=["clone3"],
    )
