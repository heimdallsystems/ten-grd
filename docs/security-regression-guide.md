# Security regression guide: tenant boundary failures

Reviewed: 20 October 2026. This guide translates published incidents into
general isolation requirements. CVEs are examples, not the product boundary.
Tests here are GRD regressions, not reproductions of the affected products.

## Evidence and attribution

### CVE-2026-40967 — filter conversion injection
Spring's vendor advisory confirms that some Spring AI vector filter converters
can change query meaning when handling unescaped keys or values. It identifies
user-influenced filter expressions as the relevant input boundary.

Source: https://spring.io/security/cve-2026-40967/

The requirement for GRD is end-to-end semantic preservation: tenant restriction
must hold both in the logical selector and in the backend request actually run.
An AST can be safe while its string serializer is vulnerable. GRD's typed AST
does not certify an arbitrary backend converter.

### CVE-2026-89032 — semantic cache isolation
VulnCheck's advisory describes cross-scope cached response disclosure and risk
from cached tool-call content. Treat the advisory's account of the metadata
helper mismatch as an attributed report, not a GRD source-code finding.

Source:
https://www.vulncheck.com/advisories/berriai-litellm-rc-1-tenant-isolation-bypass-via-semantic-cache-layer

The linked maintainer PR documents selectable end-user scoping, with key-level
scope as the default and fallback when no end-user identifier is available.
This demonstrates why tenant identity and additional user-level authorization
scope must be explicit. It does not establish GRD coverage of LiteLLM.

Maintainer context: https://github.com/BerriAI/litellm/pull/39590

## Current coverage versus required adapter evidence

| Failure family | Existing GRD behavior | Remaining evidence |
|---|---|---|
| OR/NOT widens selection to another tenant | Z3 checks supported AST; client imposes tenant predicate | Every backend must execute the same semantics |
| Filter values contain query syntax | Reference evaluator treats values as literals | Real converter parameterization/escaping tests |
| Filter key injects backend syntax | Unsupported field syntax rejected | Backend-specific accepted-name mapping |
| Foreign data returned by an adapter | Whole batch rejected before caller receives it | Backend access and side effects remain adapter responsibilities |
| Cache write/read scope differs | Redis and semantic cache adapters bind one immutable scope | Guarded cache operations and drift tests |
| Similar prompt selects a foreign completion | Qdrant semantic cache applies hard scope before similarity | Hard scope partition before similarity retrieval |
| Cached tool calls cross scope | Foreign Record payload rejected by the generic client | Actual cache and agent execution integration |
| Missing identity broadens cache scope | Missing required context denies; no cache scope fallback | Cache adapter must reject missing required scope |
| Same-tenant users have different permissions | Host owns within-tenant authorization | Cache scope must include required subject/permission dimensions |

There is no filter_applied flag or runtime decorator in the current API.
The structured selector model already goes beyond the original boolean design.
Decorating a cache helper alone cannot establish that the returned key, entry
ownership, backend selection, and executed operation agree.

## Retrieval adapter release criteria

1. Bind authenticated tenant independently of user input.
2. Keep tenant restriction outside the user expression.
3. Use backend parameter binding or a reviewed structural encoding.
4. Test quotes, escapes, delimiters, Unicode, nested boolean expressions,
   missing fields, and unsupported syntax against the real backend.
5. Compare backend results with the typed selector evaluator over seeded data.
6. Include a deliberately unsafe converter/omitted filter and show that release
   tests catch it; do not mistake a safe in-memory test for backend coverage.
7. Reject foreign provenance before reranking, LLM ingestion, streaming,
   logging, callbacks, or caching can expose it.

## Cache adapter requirements — implemented foundation and release criteria

### Scope and key construction
Create one immutable CacheScope from validated context and host authorization.
At minimum include tenant and policy version. Add subject/authorization scope,
namespace, and model/index/configuration version where they affect reuse.
Never derive authoritative identity from prompts or caller metadata aliases.

Use the same scope object for write, exact lookup, semantic lookup, refresh,
invalidation, retries, and async work. Missing or conflicting required scope
denies; do not fall back to a shared, anonymous, team, or key bucket that widens
the declared authorization boundary.

Encode the complete key tuple unambiguously, for example canonical structured
serialization with explicit field boundaries. Delimiter concatenation must not
allow tenant/key pairs such as ("a:b", "c") and ("a", "b:c") to collide.

The stored entry carries authoritative ownership, authorization scope, versions,
and provenance independent of the lookup key. Check all required dimensions
before exposing a cache hit. A key prefix or hash is not ownership evidence.

### What Z3 should establish
Model read scope, write scope, active context, entry ownership, and relevant
versions as separate values. Ask whether a returned entry can differ from the
active scope, or whether lookup/write mappings can disagree.

Use structured symbolic components when checking encoding properties. Any claim
about a concrete encoding needs a faithful encoding model and conformance tests.
Do not assume a hash is injective, infer ownership from a digest, or claim that
Z3 has verified arbitrary Python key-generation code.

Bind checked scope and key inputs to actual backend calls. Reject changed or
stale evidence. Key generation should be a deterministic projection of the
immutable scope, rather than a second helper that independently rediscovers it.

### Similarity and cached tool calls
Restrict the candidate set to the authorized scope before semantic matching.
Similarity must never authorize a hit. Validate the full returned entry before
its content or tool directives leave the cache boundary.

A cache hit does not authorize executing a tool. Resolve tool authority from
the active host context and revalidate every proposed tool call.

### Required cache acceptance cases
- Identical prompts from different tenants never reuse each other's output.
- Similar prompts from different tenants never cross the hard partition.
- Deliberately mismatched read/write metadata aliases are denied.
- Missing tenant or required subject scope never becomes a global lookup.
- Async concurrent requests, retries, refresh, and invalidation preserve scope.
- Adversarial key encodings do not merge distinct scope/input tuples.
- A backend returning a foreign or incorrectly scoped entry fails before return.
- Policy/version changes prevent stale authorization from being reused.
- Tool-call-bearing cached content cannot cross scope or supply execution authority.
- Same-tenant users separated by authorization scope remain separated.

## Broader product invariant

All guarded paths must preserve authenticated tenant ownership, including
retrieval, memory, tools, writes, caches, and delegated work. Published CVEs add
regression scenarios to this invariant. They do not define an exhaustive attack
catalogue or justify a claim of 100 percent protection.

Existing source fixes remain necessary in affected integrations. GRD does not
patch Spring AI or LiteLLM and currently has no integration with either.

## Version 0.2 implementation evidence

Redis exact caching and Qdrant semantic caching now implement the scope, envelope,
TTL, and invalidation foundation. LangChain tools bind context at invocation.
See [integration documentation](integrations.md) and the backend-parametrized
tests for the supported contract. Live services remain required to establish
server compatibility; local/fake results must be reported separately.
