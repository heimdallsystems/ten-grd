# Developer adoption roadmap

## Current foundation

Version 0.2 implements TenantGuard and tenant_scope, a typed Eq/And/Or/Not
selector AST, fail-closed Z3 checks, storage and retrieval adapters, scoped
caches, and LangChain tools. There is no runtime decorator API or raw filter
string parser. The selector model is already more than parameter checking.

## Next release priorities

1. Establish reproducible validation: core and embedded integrations on supported
   Python versions, plus PostgreSQL, Redis and Qdrant service tests. Report skips
   and exact dependency versions separately from passes.
2. Make first use easy: a source-install quickstart and a runnable tenant-scoped
   RAG example with allowed retrieval and attempted foreign access.
3. Gather developer feedback on setup, adapter fit, errors and verification
   latency. Measure latency before adding optimizations or broader syntax.
4. Expand semantics only with explicit modeling and backend conformance tests.
   Unsupported syntax continues to deny. Counterexamples and async backend APIs
   are candidates for subsequent releases, not current guarantees.

## Product direction

The open-source SDK is the developer adoption entry point. CI/CD gating and a
proprietary enterprise assessment control plane are future product directions;
neither is implemented by this runtime repository. Their evidence contracts
must be designed explicitly rather than assuming a runtime selector proof
establishes application-wide isolation.

The SDK proves the supported selector property under trusted authentication,
enforcement and adapter assumptions. It does not prove arbitrary agents,
deployment security, or complete protection from prompt injection.
