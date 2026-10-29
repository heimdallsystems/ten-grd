# Local validation — 21 October 2026

Validated against base commit 1d958dd plus the RAG example, README, roadmap and
CI example changes in this working tree.

## Environment and results

- Windows, CPython 3.13.11, isolated environment installed with `.[dev,all]`.
- pytest 9.1.1, z3-solver 5.3.1.0, qdrant-client 1.19.1, fakeredis 2.40.0,
  redis 8.1.0, psycopg 3.3.6, langchain-core 1.6.12.
- `python -m pytest -q -o faulthandler_timeout=30`: **83 passed, 15 skipped**
  in 10.50 seconds outside the restricted execution sandbox.
- `python examples/agent_memory.py`: passed.
- `python examples/tenant_rag.py`: passed, including assertions that foreign
  documents never enter the assembled answer and an explicit foreign selector
  returns no documents.

The embedded checks exercise SQLite, local Qdrant, fakeredis and LangChain.
Service variants were skipped because their connection variables were absent.
Docker's Linux engine was unavailable locally, so PostgreSQL, real Redis and
Qdrant HTTP service behavior was not validated in this run. CI configures these
services with GRD_REQUIRE_SERVICES=1; this report does not establish a CI result.

## Investigated test stall

In the restricted Windows sandbox, the run stalled in Python's asyncio event
loop initialization. A faulthandler trace located the wait in socket.accept,
called by socket._fallback_socketpair while creating the loop's self-pipe,
before the async SDK test body executed. The same suite completed outside that
sandbox. No SDK workaround or weakening of async tests was needed.

## Reproduce

```sh
python -m pip install -e ".[dev,all]"
python -m pytest -q -o faulthandler_timeout=30
```

The examples moved to the public
[ten-grd-examples](https://github.com/heimdallsystems/ten-grd-examples) repository;
CI's public-examples job runs them against this checkout.

For mandatory live-service coverage:

    docker compose -f compose.integration.yml up -d
    # Set environment variables in your shell:
    # GRD_POSTGRES_DSN=postgresql://grd:grd_test@localhost:5432/grd_test
    # GRD_REDIS_URL=redis://localhost:6379/0
    # GRD_QDRANT_URL=http://localhost:6333
    # GRD_REQUIRE_SERVICES=1
    python -m pytest -q

Use isolated test services, not production data. The compose file binds ports to
loopback and uses development-only credentials. PostgreSQL test tenants and
Qdrant test collections are unique per test; Redis keys use unique namespaces.
Tests never flush a shared Redis database or truncate unrelated SQL data.
Without service variables, service variants are explicitly skipped.
Local/fake backends do not establish deployed server compatibility or a
universal isolation guarantee.
