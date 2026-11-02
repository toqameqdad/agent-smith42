from types import SimpleNamespace

from agent.code_extractor import extract_code
from agent.orchestrator import AgentOrchestrator
from llm.token_manager import TokenManager
from models.outputs import SolutionOutput


def test_extracts_python_code_block():
    result = extract_code("Thought: inspect\n```python\nprint('hello')\n```")
    assert result.response_format == "python"
    assert result.code == "print('hello')"
    assert result.error is None


def test_extracts_anthropic_xml_tool_call():
    response = (
        '<invoke name="read_file">'
        '<parameter name="filepath">"/testbed/main.py"</parameter>'
        '<parameter name="start_line">1</parameter>'
        '</invoke>'
    )
    result = extract_code(response)
    assert result.response_format == "xml_tool_call"
    assert result.code == "read_file(filepath='/testbed/main.py', start_line=1)"


def test_extracts_hermes_json_tool_call():
    response = (
        '<tool_call>{"name":"run_tests","arguments":'
        '{"code":"def f(): return 1","test_list":["assert f() == 1"]}}'
        '</tool_call>'
    )
    result = extract_code(response)
    assert result.response_format == "json_tool_call"
    assert result.code.startswith("run_tests(code=")
    assert "test_list=" in result.code


def test_extracts_react_tool_call():
    result = extract_code(
        'Thought: search\nAction: search_code\n'
        'Action Input: {"pattern":"def solve"}'
    )
    assert result.response_format == "react"
    assert result.code == "search_code(pattern='def solve')"


def test_invalid_or_missing_code_returns_feedback():
    malformed = extract_code("```python\nif True print('bad')\n```")
    assert malformed.code
    assert "syntax error" in malformed.error.lower()
    missing = extract_code("I think the answer is 42.")
    assert missing.code == ""
    assert "No valid" in missing.error


def test_token_manager_rotates_keys():
    manager = TokenManager({"provider": ["key-a", "key-b"]})
    assert manager.get_token("provider") == "key-a"
    assert manager.rotate("provider") == "key-b"
    assert manager.rotate("provider") == "key-a"


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, messages, provider_name, max_tokens=None):
        return next(self.responses)


class FakeSandbox:
    def __init__(self):
        self.calls = []

    def execute(self, code):
        self.calls.append(code)
        if "final_answer" in code:
            return {"status": "final_answer", "answer": "def add(a, b): return a + b"}
        return {"status": "ok", "output": "exploration complete"}


def _response(content, step=1):
    return SimpleNamespace(
        content=content,
        input_tokens=10,
        output_tokens=5,
        request_time_ms=12.5,
        retries=0,
        api_url="https://provider.example/v1",
        model_name="test-model",
    )


def test_orchestrator_runs_thought_code_observation_until_final_answer():
    sandbox = FakeSandbox()
    llm = FakeLLM([
        _response("```python\nprint('explore')\n```"),
        _response("```python\nfinal_answer('def add(a, b): return a + b')\n```"),
    ])
    agent = AgentOrchestrator(
        llm_client=llm,
        sandbox=sandbox,
        provider_name="test",
        model_name="test-model",
        api_url="https://provider.example/v1",
        max_iterations=3,
    )

    result = agent.run("42", "mbpp", "Implement add(a, b).")

    assert isinstance(result, SolutionOutput)
    assert result.success is True
    assert result.solution == "def add(a, b): return a + b"
    assert result.iterations == 2
    assert result.total_requests == 2
    assert result.steps[0].sandbox_output == "exploration complete"
    assert "Observation from the real sandbox" in result.steps[1].llm_output or len(sandbox.calls) == 2
