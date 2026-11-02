"""Convert common LLM tool-call formats into sandbox-executable Python."""

import ast
import builtins
import json
import keyword
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractionResult:
    code: str = ""
    response_format: str = "none"
    error: str | None = None


def _python_value(value: Any) -> str:
    """Render JSON-compatible values as valid Python literals."""
    return repr(value)


def _tool_call(name: str, arguments: Any) -> str:
    valid_name = (
        bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))
        and not keyword.iskeyword(name)
        and not hasattr(builtins, name)
        and name not in {"final_answer", "call_mcp_tool", "sandbox_manual"}
    )
    valid_arguments = isinstance(arguments, dict) and all(
        isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        for key in arguments
    )
    if not valid_name or not valid_arguments:
        normalized = arguments if isinstance(arguments, dict) else {"input": arguments}
        return f"call_mcp_tool({name!r}, **{_python_value(normalized or {})})"
    if isinstance(arguments, dict):
        args = ", ".join(
            f"{key}={_python_value(value)}" for key, value in arguments.items()
        )
        return f"{name}({args})"
    return f"call_mcp_tool({name!r}, **{_python_value(arguments or {})})"


def _parse_xml_calls(text: str) -> list[str]:
    calls: list[str] = []
    for match in re.findall(r"<invoke\b.*?</invoke>", text, flags=re.DOTALL):
        try:
            root = ET.fromstring(match)
        except ET.ParseError:
            continue
        name = root.attrib.get("name")
        if not name:
            continue
        arguments: dict[str, Any] = {}
        for parameter in root.findall("parameter"):
            key = parameter.attrib.get("name")
            if not key:
                continue
            raw = parameter.text or ""
            try:
                arguments[key] = json.loads(raw)
            except json.JSONDecodeError:
                arguments[key] = raw
        calls.append(_tool_call(name, arguments))
    return calls


def _parse_json_calls(text: str) -> list[str]:
    calls: list[str] = []
    for raw in re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, flags=re.DOTALL):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        name = payload.get("name")
        if isinstance(name, str) and name:
            calls.append(_tool_call(name, payload.get("arguments", {})))
    return calls


def _parse_react_calls(text: str) -> list[str]:
    pattern = (
        r"Action:\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*"
        r"\nAction Input:\s*(\{.*?\}|\[.*?\]|[^\n]+)"
    )
    calls: list[str] = []
    for name, raw in re.findall(pattern, text, flags=re.DOTALL):
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {"input": raw.strip()}
        calls.append(_tool_call(name, arguments))
    return calls


def extract_code(response: str) -> ExtractionResult:
    """Extract Python or translate XML, JSON/Hermes, and ReAct tool calls."""
    if not isinstance(response, str):
        return ExtractionResult(error="The LLM response was not text.")
    blocks = re.findall(
        r"```(?:python|py)?\s*\n?(.*?)```",
        response,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if blocks:
        code = "\n\n".join(block.strip() for block in blocks if block.strip())
        if not code:
            return ExtractionResult(error="A Python code block was found but it was empty.")
        try:
            ast.parse(code)
        except SyntaxError as error:
            return ExtractionResult(
                code=code,
                response_format="python",
                error=f"Extracted Python contains a syntax error: {error}",
            )
        return ExtractionResult(code=code, response_format="python")

    xml_calls = _parse_xml_calls(response)
    if xml_calls:
        return ExtractionResult("\n".join(xml_calls), "xml_tool_call")

    json_calls = _parse_json_calls(response)
    if json_calls:
        return ExtractionResult("\n".join(json_calls), "json_tool_call")

    react_calls = _parse_react_calls(response)
    if react_calls:
        return ExtractionResult("\n".join(react_calls), "react")

    if "<invoke" in response or "<tool_call>" in response:
        return ExtractionResult(error="A tool-call block was present but could not be parsed.")
    return ExtractionResult(error="No valid Python code block or supported tool call was found.")
