# Threat model and trust boundary

## Protected property

A tenant-facing operation may only access resources owned by its authenticated
active tenant. Missing identity, unknown ownership, unsupported semantics, and
inconclusive verification deny.

## Attacker capabilities

Assume an attacker controls prompt text, ordinary request fields, tool arguments,
logical record IDs, user metadata, and selector expressions supplied to a tool.
They can repeat identifiers used by another tenant, inject OR/NOT predicates,
submit identical prompts, and issue concurrent requests.

They do not control validated host authentication, SDK code, configured engines,
adapter code, raw credentials, or authoritative ownership storage. Compromise
of those trusted components is outside the in-process SDK boundary.

## Current defenses

- No tenant override argument on guarded operations.
- Context is immutable and scoped with contextvars.
- Owner is a separate field with reserved metadata names.
- User predicates are combined beneath the mandatory tenant predicate.
- Z3 checks the actual supported selector for foreign/missing ownership.
- Solver unknown/errors and unsupported syntax deny.
- Snapshot operation digests bind verification inputs.
- The reference adapter enforces tenant partitioning atomically under a lock.
- Every result batch is validated before any content is returned.

## Deployment obligations

Authenticate tenant membership before entering tenant_scope. Do not let an LLM
create TenantContext from tool input. Protect all tenant data paths, credentials,
and authoritative owner fields; raw backend access bypasses an in-process guard.

A custom adapter must be reviewed and tested as security-critical code. A buggy
write adapter can cause damage before result validation notices it. Follow
adapter requirements in technical-specification.md before claiming support.

The synchronous client can be used by synchronous tool functions in async
request scopes, but it blocks while verifying and executing. It does not provide
a native async remote-storage API. Explicitly propagate/revalidate context for
worker jobs; avoid detached long-lived tasks carrying stale inherited context.

Tenant equality is not complete authorization. The host must separately enforce
permissions within a tenant. Payload content may contain arbitrary user-supplied
identifiers; only Record.tenant_id is authoritative ownership.

## Assurance limits

PROVEN applies to string-metadata-v1 selector logic under the trusted adapter
contract. It is not a proof of arbitrary agent behavior, infrastructure, backend
internals, side-channel resistance, or authentication correctness.

No production vector store, cache, relational database, or agent framework has
been certified by this initial implementation. Do not represent the reference
adapter as persistent storage or a deployed system-wide guarantee.

## Integration boundary in 0.2

SQLite/PostgreSQL, Qdrant, Redis, semantic cache and LangChain integrations are
now implemented. Backend clients, credentials, schema/collection provisioning,
model configuration, and the host authorization boundary remain trusted.
A supported adapter is not a certification of the backend or deployment.
The integration guide documents exact supported semantics and service tests.
UUID/hash identifiers use collision resistance as an operational assumption;
Z3 does not prove hash injectivity. Cache hits additionally validate full scope
and request binding independently of their hashed key.
