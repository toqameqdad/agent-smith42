import asyncio
import json
import shlex
import threading
from typing import Any


def _to_json_value(value: Any) -> Any:
    """Convert MCP SDK models into JSON-compatible values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {key: _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_value(item) for item in value]
    return value


class MCPClient:
    """Persistent synchronous facade over the asynchronous MCP Python client."""

    def __init__(
        self,
        stdio_command: str | None = None,
        server_url: str | None = None,
    ):
        if bool(stdio_command) == bool(server_url):
            raise ValueError("Provide exactly one MCP transport.")

        self.tools: list[dict[str, Any]] = []
        self.resources: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="mcp-client-loop",
            daemon=True,
        )
        self._thread.start()

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._connect(stdio_command, server_url),
                self._loop,
            )
            future.result(timeout=30)
        except Exception:
            self.close()
            raise

    async def _connect(
        self,
        stdio_command: str | None,
        server_url: str | None,
    ) -> None:
        from mcp import Client, StdioServerParameters

        if server_url:
            server = server_url
        else:
            parts = shlex.split(stdio_command or "")
            if not parts:
                raise ValueError("MCP stdio command cannot be empty.")
            server = StdioServerParameters(
                command=parts[0],
                args=parts[1:],
            )

        self._client = Client(server)
        await self._client.__aenter__()

        tools = await self._safe_list("list_tools", "tools")
        resources = await self._safe_list("list_resources", "resources")
        prompts = await self._safe_list("list_prompts", "prompts")

        self.tools = [_to_json_value(item) for item in tools]
        self.resources = [_to_json_value(item) for item in resources]
        self.prompts = [_to_json_value(item) for item in prompts]

    async def _safe_list(self, method_name: str, field_name: str) -> list:
        capabilities = getattr(self._client, "server_capabilities", None)
        if (
            capabilities is not None
            and getattr(capabilities, field_name, None) is None
        ):
            return []

        items = []
        cursor = None
        for _ in range(100):
            arguments = {"cursor": cursor} if cursor else {}
            result = await getattr(self._client, method_name)(**arguments)
            items.extend(getattr(result, field_name, []) or [])
            cursor = (
                getattr(result, "nextCursor", None)
                or getattr(result, "next_cursor", None)
            )
            if not cursor:
                return items

        raise RuntimeError(f"Too many pages returned by MCP {method_name}.")

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "call_tool":
            result = await self._client.call_tool(
                params["name"],
                arguments=params.get("arguments", {}),
            )
        elif method == "list_resources":
            return _to_json_value(
                await self._safe_list("list_resources", "resources")
            )
        elif method == "read_resource":
            result = await self._client.read_resource(params["uri"])
        elif method == "list_prompts":
            return _to_json_value(
                await self._safe_list("list_prompts", "prompts")
            )
        elif method == "get_prompt":
            result = await self._client.get_prompt(
                params["name"],
                arguments=params.get("arguments", {}),
            )
        else:
            raise ValueError(f"Unknown MCP operation: {method}")

        return _to_json_value(result)

    def handle_call(self, method: str, params: dict[str, Any]) -> Any:
        """Execute one MCP operation from a sandbox wrapper."""
        future = asyncio.run_coroutine_threadsafe(
            self._dispatch(method, params),
            self._loop,
        )
        return future.result(timeout=120)

    def build_manual(self) -> str:
        """Build an LLM-facing manual from the connected server's live schemas."""
        sections = ["# Connected MCP server"]

        if self.tools:
            sections.append("## Tools")
            for tool in self.tools:
                name = tool.get("name", "unknown")
                description = tool.get("description") or "No description provided."
                schema = tool.get("inputSchema") or tool.get("input_schema") or {}
                sections.append(
                    f"### {name}\n{description}\n"
                    f"Call it as a Python function using keyword arguments if the wrapper is exposed. "
                    f"If the name is invalid or conflicts with a builtin, use "
                    f"call_mcp_tool({name!r}, **arguments).\n"
                    f"Input schema:\n{json.dumps(schema, indent=2, ensure_ascii=False)}"
                )
        else:
            sections.append("No tools are exposed by this MCP server.")

        sections.append(
            "## Resources\n"
            "Use mcp_list_resources() to list resources and "
            "mcp_read_resource(uri) to read one."
        )
        for resource in self.resources:
            sections.append(
                f"- {resource.get('name', resource.get('uri', 'resource'))}: "
                f"{resource.get('description', '')} "
                f"(URI: {resource.get('uri', '')})"
            )

        sections.append(
            "## Prompts\n"
            "Use mcp_list_prompts() to list prompts and "
            "mcp_get_prompt(name, arguments={}) to retrieve one."
        )
        for prompt in self.prompts:
            sections.append(
                f"- {prompt.get('name', 'prompt')}: "
                f"{prompt.get('description', '')}"
            )

        sections.append(
            "## Sandbox functions\n"
            "- final_answer(answer_string): finish the current agent task.\n"
            "- mcp_list_resources(): list server resources.\n"
            "- mcp_read_resource(uri): read a server resource.\n"
            "- mcp_list_prompts(): list server prompts.\n"
            "- mcp_get_prompt(name, arguments={}): retrieve a server prompt."
        )
        return "\n\n".join(sections)

    def close(self) -> None:
        """Close the MCP connection and its event-loop thread."""
        client = getattr(self, "_client", None)
        if client is not None and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    client.__aexit__(None, None, None),
                    self._loop,
                )
                future.result(timeout=5)
            except Exception:
                pass
            self._client = None

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        if not self._loop.is_closed():
            self._loop.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
