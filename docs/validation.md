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
python examples/agent_memory.py
python examples/tenant_rag.py
```

For mandatory live-service coverage, follow integrations.md and configure all
three service URLs plus GRD_REQUIRE_SERVICES=1. Local/fake backends do not
establish deployed server compatibility or a universal isolation guarantee.
