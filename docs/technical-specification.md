# Tennant Guard technical specification

Version 0.2 — 20 October 2026

## Objective and audience

Uphold tenant isolation for developers building agentic tools in any environment.
GRD is a standalone SDK. Cloud policy analysis and invariant-sast are separate
products, not dependencies or the architecture for this project.

The core requirement is that protected operations never expose or modify
foreign-owned data. When isolation cannot be established, deny the operation.

## Backend integration contract

Version 0.2 adds optional SQLite/PostgreSQL storage, Qdrant retrieval, Redis
exact caching, Qdrant semantic caching, and invocation-scoped LangChain tools.
The [integration guide](integrations.md) defines setup, backend semantics,
validation, and deployment responsibilities.

## Core release contract

Python package ten_grd provides:

- immutable TenantContext supplied by the host authentication boundary;
- contextvars scope with deterministic restoration;
- a typed, immutable selector AST: Eq, And, Or, Not;
- explicit presence semantics for string metadata;
- Z3 checks of an absolute tenant isolation property;
- structured PROVEN, VIOLATION, UNKNOWN, UNSUPPORTED, and ERROR results;
- operation fingerprints covering tenant, action, selector, target, and payload;
- guarded search, get, put, and delete;
- a lock-protected in-memory reference adapter;
- complete materialized result validation before return;
- tests for adversarial and concurrent execution.

The distribution is ten-grd. No AWS dependency or integration is required.
This is a foundational reference SDK, not a claim of production backend coverage.

## Tenant identity and ownership

The host must authenticate the subject, validate authorized membership in the
active tenant, and create TenantContext. Identifier format validation does not
authenticate identity. Subject, request, and policy version are required.

Tenant IDs are case-sensitive opaque canonical identifiers; the SDK rejects
leading/trailing whitespace rather than normalizing IDs. Ownership is held in
Record.tenant_id, outside user payload and metadata. Records contain finite JSON
payloads up to 1 MiB and string metadata. Keys are (tenant_id, record_id), so the
same logical ID may exist independently in multiple tenants.

Ordinary put operations cannot assign or transfer another tenant's ownership.
Cross-tenant administration, delegation, public/shared resources, and ownership
transfer are outside this initial profile. A payload field named tenant_id is
ordinary data and never establishes ownership.

## Absolute isolation model

For active tenant t and symbolic resource d:

    Select(operation, d) => OwnerKnown(d) AND Owner(d) = t

The engine seeks a counterexample:

    Select(operation, d) AND (NOT OwnerKnown(d) OR Owner(d) != t)

SAT denies. UNSAT establishes only this property in the supported model.
UNKNOWN, including timeout/cancellation reported by Z3, denies.
Exceptions and unsupported expressions deny.

Model version string-metadata-v1 interprets each field as a presence boolean
and string value. Eq on a missing field is false; Not negates the boolean result.
Empty And is true and empty Or is false. Contradictory or empty selections are
safe but may return no data. The model contains no assumed tenant equality:
the selector must impose the restriction.

Default timeout is 1000 ms; configuration accepts 1–60000 ms. Selector limits
are 256 nodes, depth 32, field length 128, and literal length 4096. No arbitrary
SQL, backend syntax, numeric comparisons, null values, joins, or policy
expressions are translated implicitly.

The engine also verifies tenant, subject, policy and additional cache scope
dimensions through verify_scope; unknown results deny. It returns redacted violation reasons. Extracting detailed
counterexamples with safe field-level redaction is future work.

## Execution binding

TenantGuard obtains current context, adds Eq(tenant_id, active_tenant), constructs
an immutable Operation, and verifies its selector. Evidence must match the
operation digest and policy version before the adapter is called.

The digest includes operation kind, tenant, selector AST, target ID, payload
snapshot, and metadata. Evidence is checked internally and is not a reusable
authorization token or signature. Application code, injected engines, and
adapters are trusted; this mechanism is not a sandbox against their compromise.

The reference adapter uses the same selector evaluator and immutable operation,
enforces explicit tenant partitioning independently, and holds its lock through
selection or mutation. There is no separate unscoped write after a preflight
check. Get/delete cannot target more than one result; put must return the exact
record written.

The client validates all result ownership and selector membership before
returning any item. It rejects lazy results and foreign or unlabeled records.
This defense does not undo side effects of an incorrect external adapter.

## Adapter requirements

An IsolationAdapter accepts a checked immutable Operation and returns a fully
materialized tuple of Records. It is trusted enforcement code.

Before a production adapter is advertised as supported, it must demonstrate:

1. Faithful model-to-backend semantics, including missing values, types,
   collation, filtering, and pagination.
2. Atomic tenant restrictions for every advertised mutation.
3. Trusted authoritative ownership and protection against unauthorized changes.
4. Stable check/execution binding across transactions, retries, and concurrency.
5. Result validation before streaming, LLM ingestion, caching, or callbacks.
6. Adversarial tests against the real backend, not only a mocked filter flag.
7. Versioned supported-semantics documentation and explicit unsupported cases.

Relational, vector, cache, file, remote tool, and framework adapters are equally
valid extensions. None is the assumed deployment environment.

## Agentic integration requirements

An agent's prompt or tool arguments may choose data identifiers and supported
user predicates; they must not establish tenant scope. Each tool invocation uses
the host's validated context.

Async child tasks inherit context when created. Long-lived tasks and background
workers must establish a fresh validated context per job. Context does not
automatically cross process, remote-call, or arbitrary thread boundaries.

Future worker integrations require authenticated job envelopes, authorized
service identity, expiry, replay controls, and worker-side revalidation.
Delegated agents cannot expand tenant scope merely by passing a different ID.

Memory, retrieval results, generated summaries, embeddings, and cached outputs
must retain owner/provenance. Cache adapters partition writes,
lookup, refresh, invalidation, and semantic similarity by tenant and relevant
authorization versions.

## CI and release acceptance

The test suite gates the core contract and installed adapters. Service-backed
variants are mandatory in integration CI; missing service coverage fails there.
It must cover direct foreign ownership, missing identity, OR/NOT selector attacks,
write partitioning, reserved metadata, immutable payloads, unsupported syntax,
solver failures/unknown, evidence mismatch, whole-batch result validation, nested
context, async concurrency, and parallel solver contexts.

Differential tests compare generated predicates with the reference evaluator
over missing, named, and other string representatives. These test model fidelity
for this AST; they are not a production backend verification.

A future deployment coverage manifest/CLI must identify protected entry points
and adapters and report missing/skipped/unsupported coverage. No successful
isolation verdict may be issued with incomplete required coverage. A relative
baseline check must never substitute for the absolute invariant.

## Roadmap and nonclaims

Native async remote adapter APIs, signed worker contexts,
additional backend/framework integrations, deployment coverage gates,
durable audit evidence, safe detailed counterexamples, proof caching, and
performance benchmarks remain future work.

No claim is made for authentication, ordinary per-action authorization,
noninterference/timing channels, arbitrary code verification, prompt injection
prevention, raw-access bypass prevention, or whole-system mathematical safety.

CVE-2026-40967 and CVE-2026-89032 have now been checked against vendor/CNA
advisories. Their failure patterns guide regression requirements; they do not
define the scope of GRD or establish full coverage. See
[the security regression guide](security-regression-guide.md) for sources,
current coverage, and required retrieval/cache adapter acceptance tests.

## References

- [Z3 solver outcomes](https://z3prover.github.io/api/html/classz3py_1_1_solver.html)
- [Python context variables](https://docs.python.org/3/library/contextvars.html)
