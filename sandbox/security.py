from pathlib import Path
import ast
import builtins

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
    Check whether all direct imports in Python code are allowed.

    Args:
        code (str): Python source code to inspect.
        authorized_imports (list[str]): Allowed module names.

    Returns:
        bool: True if all direct imports are allowed,
        False otherwise.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not is_import_allowed(alias.name, authorized_imports):
                    return False

        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""

            if node.level != 0:
                return False

            if not is_import_allowed(module_name, authorized_imports):
                return False

    return True