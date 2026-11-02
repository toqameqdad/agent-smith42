"""Thought -> Code -> Observation agent loop."""

import time
from typing import Any, Protocol

from agent.code_extractor import extract_code
from agent.prompts import build_system_prompt
from models.metrics import StepMetrics
from models.outputs import SolutionOutput


class CompletionClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        provider_name: str,
        max_tokens: int | None = None,
    ) -> Any: ...


class Sandbox(Protocol):
    def execute(self, code: str) -> dict: ...


class AgentOrchestrator:
    """Runs one benchmark task against an LLM and a persistent sandbox."""

    def __init__(
        self,
        llm_client: CompletionClient,
        sandbox: Sandbox,
        provider_name: str,
        model_name: str,
        api_url: str,
        sandbox_manual: str = "",
        max_iterations: int = 10,
        max_input_tokens: int = 6000,
        max_output_tokens: int = 1500,
        timeout_seconds: int = 120,
    ):
        self.llm_client = llm_client
        self.sandbox = sandbox
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_url = api_url
        self.sandbox_manual = sandbox_manual
        self.max_iterations = max_iterations
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        task_id: str | int,
        benchmark: str,
        task_prompt: str,
    ) -> SolutionOutput:
        started = time.monotonic()
        system_prompt = build_system_prompt(self.sandbox_manual)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt},
        ]
        steps: list[StepMetrics] = []
        total_input = 0
        total_output = 0
        total_requests = 0
        solution = ""
        error: str | None = None
        success = False

        for step_number in range(1, self.max_iterations + 1):
            if time.monotonic() - started >= self.timeout_seconds:
                error = "Agent task timed out before the next LLM request."
                break
            remaining_output = self.max_output_tokens - total_output
            if remaining_output <= 0:
                error = "Cumulative output-token limit reached."
                break

            # A conservative character-based estimate prevents sending a prompt
            # that is already larger than the remaining cumulative input budget.
            estimated_input = sum(
                len(message.get("content", "")) for message in messages
            ) // 3
            remaining_input = self.max_input_tokens - total_input
            if estimated_input > remaining_input:
                error = (
                    "Estimated cumulative input-token limit would be exceeded; "
                    "the next LLM request was not sent."
                )
                break

            try:
                response = self.llm_client.complete(
                    messages,
                    provider_name=self.provider_name,
                    max_tokens=remaining_output,
                )
            except Exception as exception:
                error = f"LLM request failed: {exception}"
                break

            total_requests += response.retries + 1
            total_input += response.input_tokens
            total_output += response.output_tokens
            extraction = extract_code(response.content)
            sandbox_input = extraction.code
            sandbox_output = extraction.warning or ""
            final_answer = None

            if extraction.error:
                sandbox_output = (
                    f"Code extraction feedback: {extraction.error} "
                    "Return one valid Python code block or a supported tool-call format."
                )
            elif total_input > self.max_input_tokens:
                sandbox_output = "Cumulative input-token limit exceeded; task stopped."
                error = sandbox_output
            elif total_output > self.max_output_tokens:
                sandbox_output = "Cumulative output-token limit exceeded; task stopped."
                error = sandbox_output
            else:
                try:
                    result = self.sandbox.execute(sandbox_input)
                    raw_output = str(result.get("output") or "")
                    message = str(result.get("message") or "")
                    execution_output = raw_output or message or str(
                        result.get("status", "")
                    )
                    sandbox_output = "\\n".join(
                        part for part in (extraction.warning, execution_output) if part
                    )
                    if result.get("status") == "final_answer":
                        final_answer = result.get("answer", "")
                        solution = str(final_answer)
                        success = bool(solution.strip())
                        if not success:
                            error = "final_answer() returned an empty solution."
                    elif result.get("status") == "error":
                        sandbox_output = (
                            f"Sandbox error: {message or 'Unknown error'}"
                        )
                        if raw_output:
                            sandbox_output += f"\nPartial output:\n{raw_output}"
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exception:
                    sandbox_output = f"Sandbox execution failed: {exception}"

            steps.append(StepMetrics(
                step=step_number,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                request_time_ms=response.request_time_ms,
                api_url=response.api_url or self.api_url,
                model_name=response.model_name or self.model_name,
                llm_output=response.content,
                sandbox_input=sandbox_input,
                sandbox_output=sandbox_output,
                retries=response.retries,
            ))

            if final_answer is not None:
                break
            if error:
                break

            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": (
                    "Observation from the real sandbox execution:\n"
                    f"{sandbox_output or '(No output was produced.)'}\n"
                    "Use this observation to decide the next step. Do not assume "
                    "unreported tool results."
                ),
            })

        if not success and error is None:
            error = f"Maximum iterations reached ({self.max_iterations}) without final_answer()."

        return SolutionOutput(
            task_id=str(task_id),
            benchmark=benchmark,
            success=success,
            solution=solution,
            iterations=len(steps),
            total_requests=total_requests,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_time_seconds=time.monotonic() - started,
            steps=steps,
            system_prompt=system_prompt,
            error=error,
        )
