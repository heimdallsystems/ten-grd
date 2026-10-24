# Backend and agent integrations — 0.2

GRD remains environment independent. Install only the integrations needed:

    python -m pip install -e ".[postgres,qdrant,redis,langchain]"
    # Or all drivers and test dependencies:
    python -m pip install -e ".[dev,all]"

Optional drivers are imported only by their integration modules. SQLite uses
the Python standard library. The base ten_grd import needs none of these drivers.

## Persistent storage

    from ten_grd import TenantContext, TenantGuard, tenant_scope
    from ten_grd.integrations.sqlite import SQLiteAdapter

    with SQLiteAdapter("agent-memory.sqlite") as adapter:
        memory = TenantGuard(adapter)
        with tenant_scope(TenantContext("acme", "alice", "request-1")):
            memory.put("plan", {"text": "private"})
            assert memory.get("plan").data == {"text": "private"}

SQLite owns a dedicated grd_records table. Writes/deletes use explicit
transactions; tenant and record form the composite primary key. A per-instance
lock protects its connection; database transactions protect multiple instances.
Close adapters during application shutdown. Back up and protect the database
file using normal host controls. SQLite search materializes matching rows;
this release does not implement pagination, arbitrary joins, or bulk APIs.

PostgreSQL uses the same TenantGuard API:

    from ten_grd.integrations.postgres import PostgresAdapter

    adapter = PostgresAdapter(dsn_from_host_configuration)
    adapter.initialize()  # Provisioning step with schema privileges.
    memory = TenantGuard(adapter)

Use a dedicated schema/table and least-privilege runtime credentials with only
required table DML. Connections are opened per operation and transactions commit
only on success. Configure TLS, credentials, database limits, and migrations in
the host deployment. This adapter does not install RLS, implement pooling, or
verify the entire PostgreSQL authorization configuration.

Both SQL adapters bind selector values and metadata paths as parameters. SQL
NULL is converted to false for Eq, so Not(Eq(missing, value)) follows the model.
Text comparisons use explicit binary/C collation. No user SQL is accepted.
Put cannot reassign ownership; delete includes tenant scope in the mutation.

## Qdrant retrieval

    from qdrant_client import QdrantClient
    from ten_grd.integrations.qdrant import QdrantRetriever

    client = QdrantClient(url="http://localhost:6333", timeout=10)
    documents = QdrantRetriever(client, "agent_documents", 3, create=True)

    with tenant_scope(TenantContext("acme", "alice", "request-2")):
        documents.put("doc-1", {"text": "private"}, [1.0, 0.0, 0.0],
                      metadata={"kind": "manual"})
        hits = documents.search([0.99, 0.01, 0.0], limit=5)
        documents.delete("doc-1")

create=True is an explicit provisioning action; default False requires an
existing compatible collection. Only one unnamed dense cosine vector is
supported. The host owns embeddings and must use a consistent model/dimension.

Every search sends a structured tenant filter to Qdrant before ranking.
Eq/And/Or/Not map to Qdrant filter objects, not interpolated filter strings.
User metadata is nested separately from authoritative ownership. The full
returned batch is decoded and checked before it is exposed.

Point IDs use UUID5 of the canonical tenant/record tuple, avoiding ambiguous
delimiter concatenation. UUID/hash collision resistance is an operational
assumption, not a Z3-proven injectivity theorem. Use dedicated backend access
controls; the SDK cannot protect against direct credential use or tampered
collections. Server consistency/availability and embedding quality are outside
the selector proof.

The local Qdrant client is useful for development and conformance tests.
It is not evidence of distributed server behavior or a concurrency guarantee.
The service-backed test suite separately exercises the HTTP backend.

## Redis exact cache

    import redis
    from ten_grd.integrations.cache import RedisCache

    cache = RedisCache(
        redis.Redis.from_url(redis_url_from_host, socket_timeout=5),
        namespace="agent_answers", model_version="model-index-v1",
    )
    with tenant_scope(TenantContext("acme", "alice", "request-3")):
        request = {"prompt": "question", "temperature": 0, "model": "my-model"}
        cache.set(request, {"answer": "private"}, ttl_seconds=300)
        hit = cache.get(request)
        if hit is not None:
            answer = hit.data
        cache.delete(request)

Keys bind tenant, subject, policy version, namespace, model/index configuration
version, and canonical request JSON. Include all output-affecting parameters in
the request or configuration version. Always provide the host's current policy
version; GRD does not discover authorization changes automatically.

Writes use atomic Redis SET with expiration. Each entry carries full scope and
request binding independently of the hashed key, and every hit is checked.
A poisoned/wrong-scope hit raises; it never becomes a global lookup or an
unvalidated completion. CacheHit distinguishes cached JSON null from a miss.

The engine can prove the modeled selector implies all required cache scope
dimensions. It does not prove SHA-256 collision freedom or arbitrary Redis/Python
code. Envelope validation connects the lookup result to the expected binding.

Namespace/subject/policy/model changes produce separate keys. Exact invalidation
deletes only the active scope/request key; old-version keys age out by TTL.
This is not a whole-tenant purge API. Configure Redis ACLs, TLS, authentication,
capacity/eviction policy, and timeouts using the host-supplied client.

## Semantic cache

    from ten_grd.integrations.semantic_cache import SemanticCache

    cache = SemanticCache(documents, namespace="semantic_answers",
                          model_version="embedding-and-index-v1")
    with tenant_scope(TenantContext("acme", "alice", "request-4")):
        cache.set({"prompt": "question"}, [1.0, 0.0, 0.0],
                  {"answer": "private"}, ttl_seconds=300)
        hit = cache.get([0.99, 0.01, 0.0], min_score=0.9)
        cache.delete({"prompt": "question"})

Tenant, subject, policy, namespace, and configuration restrictions are hard
filters before similarity matching. Entry scope, provenance binding, and
expiration are independently checked before returning any hit. The host's
trusted clock supplies expiration time. Expired vectors are not automatically
removed; schedule backend maintenance. Expired top candidates may cause a safe
miss even when a lower-ranked valid entry exists. No cross-scope fallback exists.

Cached tool directives are data, not authority. Revalidate every proposed tool
execution under the host's current context and permissions.

## LangChain tools

    from ten_grd.integrations.langchain import memory_tools, retrieval_tool

    tools = memory_tools(memory)  # Read/search only by default.
    writable_tools = memory_tools(memory, include_writes=True)
    search_tool = retrieval_tool(documents, host_embed_query)

    # The host establishes tenant scope for EACH agent run/tool invocation.
    with tenant_scope(TenantContext("acme", "alice", "request-5")):
        value = tools[0].invoke({"record_id": "plan"})
        value = await tools[0].ainvoke({"record_id": "plan"})

Tools do not capture the tenant at construction. Schemas reject unexpected
arguments, including tenant_id; tenant identity never comes from model output.
Async tools offload the synchronous SDK using asyncio.to_thread, which propagates
the invocation's contextvars. Supply thread-safe adapters/clients. Use remote
Qdrant for concurrent production tools, not an unchecked shared local client.

This release integrates LangChain tools, not every agent framework or agent
lifecycle. The host remains responsible for authentication, tenant membership,
within-tenant permissions, cancellation, and every unguarded data path.

## Claude Agent SDK examples

See the [memory and RAG setup guide](https://github.com/Ikurira/ten-grd-examples/tree/main/claude_agents)
for host-bound in-process MCP tools, local checks, and opt-in live Claude execution.
These examples do not change the core SDK or establish live-model validation.

## Validation

Release CI runs the integration tests against real PostgreSQL, Redis and Qdrant
servers in addition to embedded backends, and fails if any of those services is
missing. SQLite, local Qdrant, fakeredis and LangChain tools are also tested
without servers; fakeredis does not establish compatibility with a deployed
Redis server.

Test your own deployment against isolated services, not production data, and
report results with backend versions and skipped coverage. No test result
establishes a universal production security guarantee.
