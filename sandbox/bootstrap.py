import sys


def run(
    user_code: str,
    security_source: str,
    authorized_imports: list[str],
    allowed_directories: list[str],
) -> None:
    """
    Install the trusted security policy, then execute user code with
    restricted builtins and configured imports.
    """

    security_namespace = {
        "__name__": "_trusted_security"
    }

    exec(
        compile(
            security_source,
            "<trusted-security>",
            "exec",
        ),
        security_namespace,
    )

    sys.stdin.close()

    security_namespace["apply_worker_seccomp_policy"]()

    user_namespace = {
        "__name__": "__main__",
        "__builtins__": security_namespace[
            "create_safe_builtins"
        ](authorized_imports, allowed_directories),
    }

    exec(
        compile(
            user_code,
            "<sandbox-user-code>",
            "exec",
        ),
        user_namespace,
    )


if __name__ == "__main__":
    raise RuntimeError(
        "Bootstrap must be started by the trusted worker."
    )
