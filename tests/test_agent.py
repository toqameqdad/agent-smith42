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
        self.stop_sequences = []

    def complete(self, messages, provider_name, max_tokens=None, stop=None):
        self.stop_sequences.append(stop)
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
    assert llm.stop_sequences == [["<end_code>", "</tool_call>"]] * 2
    assert result.steps[0].sandbox_output == "exploration complete"
    assert "Observation from the real sandbox" in result.steps[1].llm_output or len(sandbox.calls) == 2


def test_tool_call_with_builtin_name_uses_generic_dispatch():
    result = extract_code('<tool_call>{"name":"print","arguments":{"value":"hello"}}</tool_call>')
    assert result.code == "call_mcp_tool('print', **{'value': 'hello'})"


def test_provider_config_loads_multiple_keys_from_environment(monkeypatch):
    from llm.providers import ProviderConfig

    monkeypatch.setenv("KEYS_ONE", "first, second")
    monkeypatch.setenv("KEYS_TWO", "third")
    provider = ProviderConfig(
        name="test",
        base_url="https://provider.example/v1",
        model_name="model",
        api_key_env=["KEYS_ONE", "KEYS_TWO"],
    )
    assert provider.load_tokens() == ["first", "second", "third"]


def test_llm_client_rotates_api_key_after_rate_limit(monkeypatch):
    import httpx
    from llm.client import LLMClient
    from llm.providers import ProviderConfig

    monkeypatch.setenv("KEYS", "key-one,key-two")
    seen_keys = []
    seen_stop = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
                response = httpx.Response(self.status_code, request=request)
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json, headers):
            seen_keys.append(headers["Authorization"])
            seen_stop.append(json.get("stop"))
            if len(seen_keys) == 1:
                return FakeResponse(429, {})
            return FakeResponse(200, {
                "choices": [{"message": {"content": "Action: print\nAction Input: {\"value\": 1}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            })

    monkeypatch.setattr("llm.client.httpx.Client", FakeClient)
    client = LLMClient([ProviderConfig(
        name="test",
        base_url="https://provider.example/v1",
        model_name="model",
        api_key_env=["KEYS"],
        max_retries=1,
    )])

    response = client.complete(
        [{"role": "user", "content": "hello"}],
        provider_name="test",
        stop=["<end_code>", "</tool_call>"],
    )
    assert seen_keys == ["Bearer key-one", "Bearer key-two"]
    assert seen_stop == [["<end_code>", "</tool_call>"]] * 2
    assert response.retries == 1
    assert response.input_tokens == 12


def test_unclosed_python_fence_is_interpreted_with_warning():
    fence = chr(96) * 3
    result = extract_code("Thought: run this\n" + fence + "python\nprint('still valid')")
    assert result.code == "print('still valid')"
    assert result.response_format == "python_unclosed"
    assert "closing code fence was missing" in result.warning


def test_orchestrator_preserves_extraction_warning_and_partial_timeout_output():
    sandbox = FakeSandbox()
    sandbox.execute = lambda code: {
        "status": "error",
        "message": "Execution timed out.",
        "output": "partial result",
    }
    fence = chr(96) * 3
    llm = FakeLLM([
        _response("Thought: execute\n" + fence + "python\nprint('start')"),
        _response(fence + "python\nfinal_answer('solution')\n" + fence),
    ])
    agent = AgentOrchestrator(
        llm_client=llm,
        sandbox=sandbox,
        provider_name="test",
        model_name="test-model",
        api_url="https://provider.example/v1",
        max_iterations=2,
    )

    result = agent.run("task", "mbpp", "Solve it.")
    assert "closing code fence was missing" in result.steps[0].sandbox_output
    assert "Execution timed out" in result.steps[0].sandbox_output
    assert "partial result" in result.steps[0].sandbox_output


def test_extracts_json_tool_call_without_closing_stop_sequence():
    response = '<tool_call>{"name":"read_file","arguments":{"filepath":"/testbed/a.py"}}'
    result = extract_code(response)
    assert result.response_format == "json_tool_call"
    assert result.code == "read_file(filepath='/testbed/a.py')"


def test_system_prompt_variants_support_ablation():
    from agent.prompts import build_system_prompt

    standard = build_system_prompt("tool manual", "standard")
    compact = build_system_prompt("tool manual", "compact")
    assert standard != compact
    assert "tool manual" in standard
    assert "tool manual" in compact


def test_orchestrator_records_failed_llm_attempts_in_solution_trace():
    from llm.client import LLMRequestError

    class FailedLLM:
        def complete(self, messages, provider_name, max_tokens=None, stop=None):
            raise LLMRequestError(
                "provider unavailable",
                attempts=3,
                request_time_ms=1250.0,
                api_url="https://provider.example/v1",
                model_name="test-model",
            )

    sandbox = FakeSandbox()
    agent = AgentOrchestrator(
        llm_client=FailedLLM(),
        sandbox=sandbox,
        provider_name="test",
        model_name="test-model",
        api_url="https://provider.example/v1",
        max_iterations=2,
    )

    result = agent.run("task-1", "mbpp", "Solve the task.")

    assert result.success is False
    assert result.iterations == 1
    assert result.total_requests == 3
    assert result.steps[0].retries == 2
    assert result.steps[0].request_time_ms == 1250.0
    assert result.steps[0].api_url == "https://provider.example/v1"
    assert "failed after 3 attempt(s)" in result.steps[0].sandbox_output
    assert sandbox.calls == []


def test_llm_client_reports_all_failed_attempts(monkeypatch):
    import httpx
    import pytest

    from llm.client import LLMClient, LLMRequestError
    from llm.providers import ProviderConfig

    monkeypatch.setenv("KEYS", "key-one,key-two")
    seen_keys = []

    class RateLimitedResponse:
        status_code = 429

        def raise_for_status(self):
            request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    class RateLimitedClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json, headers):
            seen_keys.append(headers["Authorization"])
            return RateLimitedResponse()

    monkeypatch.setattr("llm.client.httpx.Client", RateLimitedClient)
    monkeypatch.setattr("llm.client.time.sleep", lambda seconds: None)
    client = LLMClient([ProviderConfig(
        name="test",
        base_url="https://provider.example/v1",
        model_name="test-model",
        api_key_env=["KEYS"],
        max_retries=2,
    )])

    with pytest.raises(LLMRequestError) as captured:
        client.complete(
            [{"role": "user", "content": "hello"}],
            provider_name="test",
        )

    assert captured.value.attempts == 3
    assert captured.value.retries == 2
    assert len(seen_keys) == 3
    assert seen_keys == ["Bearer key-one", "Bearer key-two", "Bearer key-one"]


def test_extracts_react_tool_call_with_nested_json_arguments():
    response = (
        "Thought: inspect the nested configuration\n"
        "Action: search_code\n"
        'Action Input: {"pattern":"target","options":{"levels":[1,2],'
        '"text":"brace } inside a string"}}\n'
        "Observation: this line is not part of the JSON input"
    )

    result = extract_code(response)

    assert result.response_format == "react"
    assert result.code == (
        "search_code(pattern='target', "
        "options={'levels': [1, 2], 'text': 'brace } inside a string'})"
    )
