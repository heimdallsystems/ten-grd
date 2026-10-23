"""Claude Agent SDK answers over tenant-filtered Qdrant retrieval. Default: offline."""
import asyncio

from claude_agent_sdk import tool
from qdrant_client import QdrantClient
from ten_grd import Eq, GuardError, TenantContext, tenant_scope
from ten_grd.integrations.qdrant import QdrantRetriever

from .common import arguments, demo_context, payload, reply, run_agent, schema, validate_strings

# Fixed toy vectors keep retrieval reproducible; these are not text embeddings.
TOPIC_VECTORS = {"refunds": [1.0, 0.0, 0.0], "support": [0.0, 1.0, 0.0]}


def make_tools(documents, context):
    if type(context) is not TenantContext:
        raise ValueError("A host-validated TenantContext is required")

    @tool("search_policy", "Retrieve the current tenant's refund or support policy.",
          schema({"topic": {"type": "string", "enum": list(TOPIC_VECTORS)}}))
    async def search_policy(args):
        try:
            validate_strings(args, {"topic": 32})
            if args["topic"] not in TOPIC_VECTORS:
                raise ValueError("Unsupported topic")
            with tenant_scope(context):
                hits = documents.search(TOPIC_VECTORS[args["topic"]],
                                        predicate=Eq("topic", args["topic"]), limit=3)
            # QdrantRetriever validates the complete result batch before this
            # callback returns anything to Claude. Do not stream partial hits.
            return reply({"documents": [
                {"record_id": hit.record.record_id, "text": hit.record.data["text"]}
                for hit in hits
            ]})
        except (ValueError, GuardError):
            return reply({"error": "Retrieval denied"}, error=True)

    return [search_policy]


def seed_documents(client):
    documents = QdrantRetriever(client, "claude_demo", 3, create=True)
    for tenant, days, vector in (
        ("acme", 30, [0.99, 0.01, 0.0]),
        ("other", 90, [1.0, 0.0, 0.0]),
    ):
        with tenant_scope(demo_context(tenant)):
            documents.put("refund-policy", {"text": f"{tenant} refunds within {days} days."},
                          vector, metadata={"topic": "refunds"})
    return documents


async def local_check(documents):
    acme = make_tools(documents, demo_context("acme"))[0]
    other = make_tools(documents, demo_context("other"))[0]
    a = payload(await acme.handler({"topic": "refunds"}))["documents"]
    b = payload(await other.handler({"topic": "refunds"}))["documents"]
    assert [item["text"] for item in a] == ["acme refunds within 30 days."]
    assert [item["text"] for item in b] == ["other refunds within 90 days."]
    assert not payload(await acme.handler({"topic": "support"}))["documents"]
    override = await acme.handler({"topic": "refunds", "tenant_id": "other"})
    assert override["is_error"]
    print("PASS: closer foreign document excluded; empty results and override handled. "
          "No Claude call made.")


async def main():
    args = arguments(__doc__)
    client = QdrantClient(location=":memory:")
    try:
        documents = seed_documents(client)
        await local_check(documents)
        if args.live:
            for tenant in ("acme", "other"):
                print(f"\nHost-bound tenant: {tenant}")
                await run_agent(make_tools(documents, demo_context(tenant)),
                                "Use search_policy to find our refund policy. "
                                "Answer using only the returned documents and cite their record IDs.",
                                model=args.model)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
