import sys
import json


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
    security_namespace["apply_landlock"](
        allowed_directories,
        ["/bin", "/lib", "/lib64"],
    )

    user_namespace = {
        "__name__": "__main__",
        "__builtins__": security_namespace[
            "create_safe_builtins"
        ](authorized_imports, allowed_directories),
    }

    class FinalAnswerSignal(Exception):
        def __init__(self, answer: str):
            self.answer = answer

    def final_answer(answer: str) -> None:
        if not isinstance(answer, str):
            raise TypeError("final_answer expects a string")
        raise FinalAnswerSignal(answer)

    user_namespace["final_answer"] = final_answer

    try:
        exec(
            compile(
                user_code,
                "<sandbox-user-code>",
                "exec",
            ),
            user_namespace,
        )
    except FinalAnswerSignal as signal:
        sys.stdout.write(
            "__AGENT_SMITH_FINAL_ANSWER__:"
            + json.dumps(signal.answer)
            + "\n"
        )


if __name__ == "__main__":
    raise RuntimeError(
        "Bootstrap must be started by the trusted worker."
    )
