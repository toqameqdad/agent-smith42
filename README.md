*This activity has been created as part of the 42 curriculum by toqameqdad.*

# Agent Smith

## Description

Agent Smith is a code-agent project built for the 42 curriculum. The goal is to build an agent that can reason about a coding task, use tools to inspect and modify a codebase, execute Python safely, observe results, and iterate toward a solution.

The project targets two coding benchmarks:

- **MBPP** — Python programming problems.
- **SWE-bench** — real-world software engineering issues.

The current implementation focuses on the sandbox execution layer and its MCP integration. The benchmark agents, orchestration loop, and benchmark evaluation are still under development.

## Instructions

### Requirements

- Linux environment with Python 3.10.
- `uv` package manager.
- Linux user, mount, and network namespace support.
- Landlock and Seccomp support in the Linux kernel.
- `libseccomp.so.2` and the `unshare`, `mount`, and `chroot` utilities.

The sandbox deliberately fails closed when required kernel isolation features are unavailable.

### Installation

```bash
uv sync --dev
```

### Run the interactive sandbox

```bash
uv run sandbox
```

Load a custom JSON configuration:

```bash
uv run sandbox config/sandbox_template.json
```

The REPL keeps one Python namespace between entries. Enter `exit` or press Ctrl+D to leave.

### Connect an MCP server

Connect to a server launched through stdio:

```bash
uv run sandbox --mcp-stdio "python path/to/mcp_server.py" config/sandbox_template.json
```

Connect to a Streamable HTTP MCP server:

```bash
uv run sandbox --mcp-server http://localhost:8000/mcp
```

The connected server's tools, resources, prompts, and tool input schemas are discovered at startup. The generated manual is printed in the terminal and exposed inside the sandbox as `sandbox_manual`.

MCP tools with valid, non-conflicting Python identifiers are injected as callable functions. Use `call_mcp_tool("exact-tool-name", key=value)` for names that cannot be exposed directly.

### Sandbox functions

- `final_answer(answer_string)` — captures the final answer and signals task completion.
- `mcp_list_resources()` — lists resources exposed by the connected MCP server.
- `mcp_read_resource(uri)` — reads a resource.
- `mcp_list_prompts()` — lists prompts exposed by the connected MCP server.
- `mcp_get_prompt(name, arguments={})` — retrieves a prompt.
- Each discovered MCP tool is exposed as a Python function when its name can safely be used as an identifier.

## System architecture

The current sandbox execution path is:

```text
CLI / SandboxSession
        |
        v
unshare (user + mount + network namespaces)
        |
        v
Trusted sandbox worker
        |
        v
chrooted Python runtime
        |
        +--> Seccomp syscall policy
        |
        +--> Landlock filesystem policy
        |
        +--> Restricted Python builtins and imports
        |
        +--> Persistent user namespace
```

MCP connections are owned by the trusted host-side client. Calls made by sandbox wrappers are sent through a JSON-lines bridge to that client; the sandboxed Python process does not receive direct network access.

The intended full agent architecture adds an LLM provider and an agent orchestrator around this execution layer. Those components are not yet implemented.

## Agent loop

The intended agent loop is:

1. Build the task prompt and include the dynamically generated sandbox manual.
2. Ask the selected LLM to produce executable Python code.
3. Execute that code in the sandbox.
4. Return stdout, errors, and MCP tool results to the LLM as observations.
5. Repeat until the code calls `final_answer(answer_string)` or an agent limit is reached.
6. Convert the captured answer and execution metrics into the benchmark output format.

The orchestration and benchmark-output pipeline are still in progress; the steps above describe the target design, not a completed end-to-end agent.

## Sandbox design

Sandbox behavior is configured with the Pydantic `SandboxConfig` model and JSON files.

| Setting | Purpose |
| --- | --- |
| `authorized_imports` | Allowlist of importable Python modules and submodules. |
| `allowed_directories` | Directories visible to sandboxed code for filesystem access. |
| `max_execution_time_seconds` | Maximum execution time for a sandboxed code entry. |
| `max_memory_mb` | Address-space memory limit for the sandbox process. |

The isolation layer combines:

- A chrooted Python runtime.
- Private user, mount, and network namespaces.
- Read-only runtime mounts, with configured workspace directories mounted writable.
- Landlock filesystem rules that grant access only to runtime paths and configured directories.
- Seccomp restrictions for dangerous worker operations and network-related syscalls.
- Restricted builtins and import validation.

MCP calls are delegated to the trusted host process through the bridge. This is intentional: the sandboxed code can use explicitly exposed MCP capabilities without opening arbitrary network sockets.

## Tool implementation details

The MCP client supports:

- stdio servers launched from a command and argument string.
- Streamable HTTP servers.
- Dynamic discovery of tools, resources, prompts, and paginated tool/resource/prompt lists.
- Tool wrappers callable from sandbox Python.
- A generated manual containing tool descriptions and input schemas.

The benchmark-specific MCP server scripts and the mandatory filesystem, code-search, and execution tools are still being developed.

## Benchmark results and analysis

No benchmark runs have been completed yet. MBPP and SWE-bench results will be added after the agent loop, benchmark adapters, and evaluation pipeline are implemented and tested. No success rate or performance claim is made at this stage.

## Resources

- [Python 3.10 documentation](https://docs.python.org/3.10/)
- [Python subprocess documentation](https://docs.python.org/3.10/library/subprocess.html)
- [Linux Landlock documentation](https://docs.kernel.org/userspace-api/landlock.html)
- [Linux Seccomp filter documentation](https://docs.kernel.org/userspace-api/seccomp_filter.html)
- [Model Context Protocol documentation](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

### Use of AI

ChatGPT was used to assist with sandbox architecture review, implementation and debugging of the filesystem and syscall restrictions, MCP client/bridge implementation, test design, and documentation drafting. AI-generated suggestions were treated as implementation assistance; benchmark results and project behavior are not claimed without test evidence.
