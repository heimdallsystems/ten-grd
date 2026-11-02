# Ten-Grd

Tenant isolation guards for developers building agentic tools.

Ten-Grd keeps the authenticated tenant attached to every protected data
operation. Its Z3 engine checks whether a selector could include another
tenant's data or unlabeled data. The guarded client adds the tenant restriction,
checks before execution, and validates complete result batches before returning
them. When isolation cannot be established, the operation is denied.

## Install

Requires Python 3.10 or later:

```sh
python -m pip install ten-grd
```

The distribution is named `ten-grd`; the Python import is `ten_grd`. Optional
backends install as extras; see [Integrations](integrations.md).

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

Constructing `TenantContext` validates identifier format; it does **not**
authenticate a user or establish tenant membership. Never construct it from an
unchecked prompt, tool argument, or request field. The guarded methods expose no
tenant parameter, so a tool argument cannot override the tenant.

## Selectors and formal checks

```python
from ten_grd import Eq, Or, TenantContext, VerificationStatus, Z3InvariantEngine

engine = Z3InvariantEngine(timeout_ms=1000)
context = TenantContext("acme", "alice", "request-3")

safe = engine.verify_selector(context, Eq("tenant_id", "acme"))
assert safe.status is VerificationStatus.PROVEN

unsafe = engine.verify_selector(
    context, Or((Eq("tenant_id", "acme"), Eq("tenant_id", "other")))
)
assert unsafe.status is VerificationStatus.VIOLATION
```

The selector language supports Eq, And, Or, and Not over string metadata.
Only PROVEN permits execution. A violation, an inconclusive or timed-out solver,
an unsupported expression, or an error all deny. There is no soft-fail option.

## Assurance boundary

A proof applies to the supported selector model. It does not prove arbitrary
agent code, backend implementations, prompts, or deployment security. The host
authentication boundary, the guarded client and storage adapters are trusted;
an in-process SDK cannot stop application code that bypasses it with raw
credentials. Ten-Grd does not prevent prompt injection.

Read the [threat model](threat-model.md) before deploying, and the
[technical specification](technical-specification.md) before writing a custom
adapter.

## Next steps

- [Examples](examples.md): runnable scripts, including Claude Agent SDK agents.
- [Leak probe](probe.md): check an agent you already built for cross-tenant
  leaks, whether or not it uses Ten-Grd (preview).
- [Integrations](integrations.md): PostgreSQL, SQLite, Qdrant, Redis and LangChain.
- [Security regression guide](security-regression-guide.md): attack patterns
  drawn from published advisories.

Ten-Grd is licensed under Apache-2.0 and published on
[PyPI](https://pypi.org/project/ten-grd/).
