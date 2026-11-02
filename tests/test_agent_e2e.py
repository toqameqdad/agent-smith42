from types import SimpleNamespace

from agent.orchestrator import AgentOrchestrator
from sandbox.config import SandboxConfig
from sandbox.executor import SandboxSession


class OneShotLLM:
    def complete(self, messages, provider_name, max_tokens=None):
        return SimpleNamespace(
            content="Thought: submit the completed solution\n"
            + chr(96) * 3
            + "python\nfinal_answer('def add(a, b): return a + b')\n"
            + chr(96) * 3,
            input_tokens=20,
            output_tokens=15,
            request_time_ms=5.0,
            retries=0,
            api_url="https://provider.example/v1",
            model_name="test-model",
        )


def test_agent_loop_completes_through_real_sandbox():
    config = SandboxConfig(max_execution_time_seconds=10)
    with SandboxSession(config) as sandbox:
        agent = AgentOrchestrator(
            llm_client=OneShotLLM(),
            sandbox=sandbox,
            provider_name="test",
            model_name="test-model",
            api_url="https://provider.example/v1",
            max_iterations=2,
        )
        result = agent.run("1", "mbpp", "Implement add(a, b).")

    assert result.success is True
    assert result.solution == "def add(a, b): return a + b"
    assert result.iterations == 1
    assert result.total_input_tokens == 20
    assert result.total_output_tokens == 15
    assert result.steps[0].sandbox_input == (
        "final_answer('def add(a, b): return a + b')"
    )


def test_mbpp_mcp_tool_executes_assertions_in_real_sandbox():
    import sys

    from sandbox.mcp_client import MCPClient

    with MCPClient(stdio_command=f"{sys.executable} mcp_tools_mbpp.py") as client:
        result = client.handle_call(
            "call_tool",
            {
                "name": "run_tests",
                "arguments": {
                    "code": "def add(a, b): return a + b",
                    "test_list": ["assert add(20, 22) == 42"],
                },
            },
        )

    assert result["isError"] is False
    assert '"success": true' in str(result).lower()
