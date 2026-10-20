# Tennant Guard (10-grd)

Tenant isolation primitives for developers building agentic tools in any environment.

GRD keeps the authenticated tenant scope attached to protected data operations.
Its Z3 engine checks whether a supported selector can include foreign-owned or
unlabeled data. The guarded client adds tenant scope, checks before execution,
and validates complete result batches before returning them.

This is a standalone project with its own audience and architecture. It has no
dependency on invariant-sast or AWS.

## Install from source

Requires Python 3.10 or later:

```sh
git clone https://github.com/Ikurira/10_grd.git
cd 10_grd
python -m pip install -e ".[dev]"
python -m pytest
python examples/agent_memory.py
```

The distribution is named `ten-grd`; the Python import is `ten_grd`.
This repository does not imply that a release has been published to PyPI.

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

The first release includes an in-memory reference adapter, not a production
database, vector-store, cache, framework, or authentication integration.
See [the specification](docs/technical-specification.md) and
[the threat model](docs/threat-model.md) for extension requirements.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q src examples
```

Tests cover foreign access, tenant scope injection, result leakage, write
partitioning, async/thread isolation, inconclusive solvers, evidence binding,
immutable snapshots, and differential checks of generated selector expressions.

Licensed under Apache-2.0.
