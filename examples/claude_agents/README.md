# Claude Agent SDK examples

Two small harnesses show how Claude can use Ten-Grd through the official
[Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python).
They use in-process MCP tools and synthetic tenant data.

| Example | What Claude does in live mode | What Ten-Grd checks |
| --- | --- | --- |
| [Memory agent](memory_agent.py) | Reads a private plan, writes a summary and reads it back. | Reads and writes stay within the host-bound tenant. |
| [RAG agent](rag_agent.py) | Calls a policy retrieval tool and answers from returned documents. | A foreign tenant's closer vector is excluded before results reach Claude. |

The scripts are repository examples, not files installed by `pip install ten-grd`.
The base SDK does not require Claude, an API key or the Claude Agent SDK.

## Install

From the repository root, using Python 3.10 or later in a virtual environment:

```sh
python -m pip install ten-grd
python -m pip install -r examples/claude_agents/requirements.txt
```

For development against this checkout, replace the first command with
`python -m pip install -e .`. The example dependencies are pinned to the versions
used for local validation: Claude Agent SDK 0.2.172, MCP 2.3.0 and Qdrant client
1.19.1.

## Check locally without calling Claude

Run from the repository root:

```sh
python -m examples.claude_agents.memory_agent
python -m examples.claude_agents.rag_agent
```

Both commands default to local checks. They call the actual tool handlers with
Ten-Grd and, for RAG, an in-memory Qdrant database. They need no API key and do not
launch Claude. A PASS means the deterministic tool checks succeeded, not that a
live model run was performed.

The memory check reads the same record ID under two tenants, rejects an injected
`tenant_id` argument, and verifies that a new note is unavailable to the other
tenant. The RAG check verifies separate answers, exclusion of a closer foreign
document, an empty result for missing policy data, and rejection of a tenant
override.

## Run with Claude

Configure authentication as described in Anthropic's
[Agent SDK quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart).
For direct API access, set `ANTHROPIC_API_KEY` in the process environment without
committing it. The SDK also supports provider configuration such as Amazon
Bedrock. These scripts do not load `.env` files automatically.

Pass a model ID available to your provider, or set `ANTHROPIC_MODEL`:

```sh
python -m examples.claude_agents.memory_agent --live --model YOUR_MODEL_ID
python -m examples.claude_agents.rag_agent --live --model YOUR_MODEL_ID
```

Each command runs the local checks first, then two separate Claude sessions,
one per synthetic tenant. Live mode sends the synthetic prompt and tool results
to your configured model provider and incurs API charges. Each session has a
six-turn limit and a configured USD 0.50 budget; these are SDK limits rather than
a billing guarantee. No live run is triggered by importing these examples.

Most supported platform wheels bundle the Claude Code binary. If your platform's
installation does not include it, follow Anthropic's installation instructions.

## Where the tenant boundary lives

1. The host authenticates the user, checks tenant membership and permissions,
   and creates a `TenantContext`. `demo_context()` supplies synthetic identities
   only; it is not an authentication implementation.
2. The host creates a fresh set of tools bound to that context for each session.
   No tool accepts a tenant identifier. JSON schemas reject extra properties;
   handlers independently validate arguments, including direct calls.
3. Every callback establishes `tenant_scope(context)` before calling Ten-Grd.
   It does not depend on an outer task's context propagating through the SDK.
4. Ten-Grd applies its mandatory tenant selector and verification. Complete
   validated results are returned to Claude. Guard failures return a generic
   error without exposing the rejected data.
5. Each tenant gets a separate client, server and temporary working directory.
   The examples never resume another tenant's conversation.

The SDK configuration removes built-in tools (`tools=[]`), uses only the
provided MCP server (`strict_mcp_config=True`), disables settings/skill/plugin
loading, and denies other tool permission requests. `allowed_tools` pre-approves
the intended MCP tools; it is not itself a tool restriction.

The memory demo gives its synthetic users permission to write their own notes.
Real applications must enforce per-action authorization or omit write tools for
read-only users. Never let a prompt choose the host context, reuse a tool server
across tenants, or share a conversation/session ID between tenants.

## Limits

The RAG example uses a fixed topic-to-vector mapping for reproducibility, not a
production embedding model. It retrieves refund/support topics and lets Claude
generate the final answer in live mode. A real application supplies its own
embedding and ranking design. Local Qdrant checks do not validate a distributed
Qdrant deployment.

Ten-Grd checks its supported data-selection model under trusted host and adapter
assumptions. It does not prove Claude's answers, prevent prompt injection, or
sandbox the process. Retrieved documents remain untrusted; host code must
authorize subsequent actions. Restrictive tool configuration does not replace
process isolation, secret management or protection of logs and transcripts.
The synchronous demo callbacks are deliberately small and may block the event
loop; a production host must design concurrency and cancellation appropriately.

## Validate the example boundary

```sh
python -m pip install pytest
python -m pytest -q tests/test_claude_agent_examples.py
```

The tests exercise real SDK MCP tool dispatch without model calls, tenant
overrides, scoped reads/writes, failed verification, invalid backend results,
and separate host contexts. Live model behaviour must be checked separately.

Official references:
- [Custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)
- [Python SDK reference](https://code.claude.com/docs/en/agent-sdk/python)
- [Permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
