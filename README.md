# Tennant Guard (10-grd)

Tenant isolation primitives for developers building agentic tools in any environment.

GRD keeps the authenticated tenant scope attached to protected data operations.
Its Z3 engine checks whether a supported selector can include foreign-owned or
unlabeled data. The guarded client adds tenant scope, checks before execution,
and validates complete result batches before returning them.

This is a standalone project with its own audience and architecture. It has no
dependency on invariant-sast or AWS.

## Install

Requires Python 3.10 or later:

```sh
python -m pip install ten-grd
```

The distribution is named `ten-grd`; the Python import is `ten_grd`.
Runnable examples, including Claude Agent SDK agents, are in the public
[ten-grd-examples](https://github.com/heimdallsystems/ten-grd-examples) repository, and the
documentation is at [heimdallsystems.github.io/ten-grd-examples](https://heimdallsystems.github.io/ten-grd-examples/).

## Use in an agent memory tool

```python
from ten_grd import InMemoryAdapter, TenantContext, TenantGuard, tenant_scope

memory = TenantGuard(InMemoryAdapter())

# Your host authenticates the subject and validates tenant membership first.
with tenant_scope(TenantContext("acme", "alice", "request-1")):
    memory.put("plan", {"text": "Acme's private plan"}, metadata={"kind": "plan"})
    assert memory.get("plan").data["text"] == "Acme's private plan"

with tenant_scope(TenantContext("other", "bob", "request-2")):
    assert memory.get("plan") is None
```

Tenant IDs in these examples are already authenticated by the host.
Constructing TenantContext validates identifier format; it does **not**
authenticate a user or establish tenant membership. Never construct it from an
unchecked prompt, tool argument, or request field.

Tool implementations can call `memory.get`, `search`, `put`, and `delete`
inside the host-established scope. A tool argument cannot override the tenant
parameter because the guarded methods expose no such parameter.

## Try tenant-scoped RAG

Run a local vector retrieval and answer assembly example without API keys:

```sh
git clone https://github.com/heimdallsystems/ten-grd-examples.git
cd ten-grd-examples
python -m pip install -r requirements.txt
python basic/tenant_rag.py
```

The example seeds two tenants with the same document ID. A foreign document has
the closest vector, but only the active tenant's document reaches the answer.
It also demonstrates an OR expression attempting to widen access and an explicit
foreign-tenant selector returning no documents. Fixed vectors and extractive
answers keep the demo reproducible; production embeddings and LLM generation
remain host integrations. Retrieved content is still untrusted input.

## Selectors and formal checks

```python
from ten_grd import Eq, Or, VerificationStatus, Z3InvariantEngine

engine = Z3InvariantEngine(timeout_ms=1000)
context = TenantContext("acme", "alice", "request-3")

safe = engine.verify_selector(context, Eq("tenant_id", "acme"))
assert safe.status is VerificationStatus.PROVEN

unsafe = engine.verify_selector(
    context, Or((Eq("tenant_id", "acme"), Eq("tenant_id", "other")))
)
assert unsafe.status is VerificationStatus.VIOLATION
```

The public selector language supports Eq, And, Or, and Not over string metadata.
Missing fields are modeled explicitly. Raw SQL, filter strings, caller-supplied
`filter_applied` flags, and unsupported expression types are not security evidence.

The guarded client always places user selectors beneath its own tenant predicate.
A user selector that requests another tenant returns no data; it never replaces
the mandatory tenant restriction.

## What this release protects

The included in-memory adapter provides atomic, tenant-partitioned create/replace,
read, search, and delete operations. Record ownership is separate from user data.
Payloads are JSON snapshots; reserved metadata cannot override tenant identity.
Context uses contextvars; the engine creates a separate Z3 context per check.

Only PROVEN permits execution. SAT is a violation; UNKNOWN (including solver
timeouts), unsupported expressions, and errors deny. Result validation occurs
before returning a batch. There is no runtime soft-fail option.

## Assurance boundary

A proof applies to the supported selector model. It does not prove arbitrary
agent code, backend implementations, prompts, or deployment security. An empty
selector can be safely unsatisfiable; this is not evidence of useful access.

The host authentication boundary, enforcement client, and storage adapter are
trusted. An in-process SDK cannot prevent hostile application code from using
raw credentials or bypassing the client. Custom adapters must execute exactly the
checked operation and uphold ownership atomically. Read-result validation cannot
undo an unsafe write by a faulty adapter.

Version 0.2 adds SQLite/PostgreSQL storage, Qdrant retrieval, Redis exact caching,
Qdrant semantic caching, and LangChain tools. Authentication remains the host
responsibility.

## Incident-inspired regression requirements

Tests use verified Spring AI filter-injection and LiteLLM cache-isolation
advisories to define general attack patterns and adapter acceptance criteria.
These are regression guides, not claims of complete CVE mitigation or
certification of affected third-party products.

## Integrations

Install optional backends as extras:

| Backend | Install |
| --- | --- |
| PostgreSQL storage | `python -m pip install "ten-grd[postgres]"` |
| Qdrant retrieval and semantic cache | `python -m pip install "ten-grd[qdrant]"` |
| Redis exact cache | `python -m pip install "ten-grd[redis]"` |
| LangChain tools | `python -m pip install "ten-grd[langchain]"` |
| All of the above | `python -m pip install "ten-grd[all]"` |

SQLite storage needs no extra.

## Claude Agent SDK examples

The [Claude memory and RAG examples](https://github.com/heimdallsystems/ten-grd-examples/tree/main/claude_agents)
use host-bound MCP tools and synthetic tenant data. Both provide deterministic
local checks and opt-in live Claude execution; see their setup guide for commands
and assurance limits.

## Probe your agent for tenant leaks (preview)

`ten-grd probe` checks an agent you already built, whether or not it uses ten-grd.
It plants a unique canary record in each synthetic test tenant, signs in as one
tenant, tries to reach the others' canaries (direct questions, instruction
injection, the other tenant's exact text, tenant-switch requests, and hostile tool
arguments), and reports every canary that crosses over:

```sh
ten-grd probe init        # writes tenguard_probe.py: fill in sign_in, seed and ask
ten-grd probe run --out leak-report.md --json leak-report.json
```

Exit codes: 0 no leak found, 1 leak found, 2 inconclusive (for example, a tenant
could not reach its own canary, so a clean result would mean nothing). The verdict
comes only from canaries; no model judges whether data leaked.

Run it against a test environment with synthetic tenants, never production. A
finding proves a leak for that input; no finding means the attempted attacks did
not leak and is not proof of isolation. The probe exercises behaviour through your
harness and does not verify source code.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q src
```

Tests cover foreign access, tenant scope injection, result leakage, write
partitioning, async/thread isolation, inconclusive solvers, evidence binding,
immutable snapshots, and differential checks of generated selector expressions.

Licensed under Apache-2.0.
