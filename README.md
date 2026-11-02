*This activity has been created as part of the 42 curriculum by toqameqdad.*

# Agent Smith

## Description

Agent Smith is a code-agent project built for the 42 curriculum. The goal is to build an agent that can reason about a coding task, use tools to inspect and modify a codebase, execute Python safely, observe results, and iterate toward a solution.

The project targets two coding benchmarks:

- **MBPP** — Python programming problems.
- **SWE-bench** — real-world software engineering issues.

The repository now includes the isolated sandbox, shared Thought → Code → Observation orchestration, LLM provider abstraction with API-key rotation, response-format extraction, mandatory SWE-bench MCP tools, and MBPP/SWE-bench CLI entry points. End-to-end benchmark validation and the required comparative benchmark report are still outstanding.

## Instructions

### Requirements

- Linux environment with Python 3.10.
- `uv` package manager.
- Linux user, mount, and network namespace support.
- Landlock and Seccomp support in the Linux kernel.
- `libseccomp.so.2` and the `unshare`, `mount`, and `chroot` utilities.

The sandbox deliberately fails closed when required kernel isolation features are unavailable.

### Tests

Run the unit and integration suite:

```bash
uv run pytest -q
```

GitHub Actions runs the unit suite (including live MCP stdio and Streamable HTTP
server tests) and runs the isolated worker plus MCP-over-stdio sandbox integration
inside a privileged Linux container, where user, mount, and network namespaces are
available.

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
uv run sandbox sandbox_template.json
```

The REPL keeps one Python namespace between entries. Enter `exit` or press Ctrl+D to leave.

### Connect an MCP server

Connect to a server launched through stdio:

```bash
uv run sandbox --mcp-stdio "python path/to/mcp_server.py" sandbox_template.json
```

Connect to a Streamable HTTP MCP server:

```bash
uv run sandbox --mcp-server http://localhost:8000/mcp
```

The connected server's tools, resources, prompts, and tool input schemas are discovered at startup. The generated manual is printed in the terminal and exposed inside the sandbox as `sandbox_manual`. MCP tool execution time is excluded from the sandbox's per-code execution budget.

MCP tools with valid, non-conflicting Python identifiers are injected as callable functions. Use `call_mcp_tool("exact-tool-name", key=value)` for names that cannot be exposed directly.

#

### Run the model benchmark matrix

Copy `benchmark_config.example.json`, replace all placeholder model IDs and task paths with
currently available free-tier models and the same three SWE-bench task files, then export the
listed API-key environment variables. Each task should include a short selection rationale.

```bash
uv run python benchmark_runner.py --config benchmark_config.json
```

The runner enforces at least two providers, five models, and three shared tasks. It runs the
standard prompt for every model/task pair, then runs the configured compact-prompt ablation
for the selected model on the same tasks. It saves each trace as
`benchmark_results/.../solution.json` and regenerates `BENCHMARK_REPORT.md`. The example
model IDs are placeholders, not claims about current free-tier availability.

### Run the MBPP agent

The evaluation environment must provide the selected provider's API key through an environment variable. Multiple comma-separated keys can be supplied in one variable, or several variable names can be listed with `--api-key-env`.

```bash
uv run python agent_mbpp.py \
  --task-file ../cache/mbpp_task.json \
  --output ../cache/mbpp_solution.json \
  --model-name "provider/model-name" \
  --provider-url "https://provider.example/v1" \
  --api-key-env "OPENROUTER_API_KEY,OPENROUTER_API_KEY_2"
```

The MBPP agent uses at most 10 iterations, 6,000 cumulative input tokens, 1,500 cumulative output tokens, and 120 seconds per task.

### Run the SWE-bench agent

Docker must be installed and available to the current user. The task JSON must contain the image and evaluation script supplied by the moulinette.

```bash
uv run python agent_swebench.py \
  --task-file ../cache/swebench_task.json \
  --output ../cache/swebench_solution.json \
  --model-name "provider/model-name" \
  --provider-url "https://provider.example/v1" \
  --api-key-env "OPENROUTER_API_KEY,OPENROUTER_API_KEY_2"
```

The SWE-bench agent starts the task image, copies the repository workspace for scoped host-side file tools, routes commands and tests into the container, and removes the container when the run exits. Its limits are 30 iterations, 300,000 cumulative input tokens, 10,000 cumulative output tokens, and 900 seconds per task.

The MCP servers can also be started independently:

```bash
uv run python mcp_tools_mbpp.py
uv run python mcp_tools_swebench.py
uv run python mcp_tools_swebench.py --transport streamable-http --port 8000
```

## Sandbox functions

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

The LLM provider and agent orchestrator are implemented around this execution layer. The MBPP and SWE-bench command-line entry points connect the task loader, provider, MCP server, sandbox, and output model.

## Agent loop

The intended agent loop is:

1. Build the task prompt and include the dynamically generated sandbox manual.
2. Ask the selected LLM to produce executable Python code.
3. Execute that code in the sandbox.
4. Return stdout, errors, and MCP tool results to the LLM as observations.
5. Repeat until the code calls `final_answer(answer_string)` or an agent limit is reached.
6. Convert the captured answer and execution metrics into the benchmark output format.

The shared orchestration loop and benchmark output model are implemented. Actual solve-rate claims remain pending until the agents are run against the moulinette evaluation tasks.

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

The repository contains separate MBPP and SWE-bench MCP server scripts. The SWE-bench server exposes the mandatory filesystem, code-search, and execution tools; the MBPP server exposes sandboxed candidate-test execution.

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
