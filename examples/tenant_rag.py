"""Tenant-scoped retrieval and answer assembly, without API keys or services.

Install: python -m pip install -e ".[qdrant]"
Run: python examples/tenant_rag.py
"""
from qdrant_client import QdrantClient

from ten_grd import Eq, Or, TenantContext, tenant_scope
from ten_grd.integrations.qdrant import QdrantRetriever


def answer_question(documents, query_vector, *, predicate=None):
    """Only validated retrieval hits may reach the answer stage.

    Production hosts supply embeddings and can replace this extractive answer
    with an LLM call using the same validated context. Retrieval does not make
    document instructions trustworthy or authorize tool execution.
    """
    hits = documents.search(query_vector, predicate=predicate, limit=3)
    if not hits:
        return "No accessible documents."
    return "\n".join(
        f"[{hit.record.record_id}] {hit.record.data['text']}" for hit in hits
    )


def main():
    # Fixed vectors make this example reproducible. They are not a text
    # embedding model. No prompts or documents are sent to external services.
    client = QdrantClient(location=":memory:")
    try:
        documents = QdrantRetriever(client, "demo_documents", 3, create=True)
        # These identities stand in for host-verified tenant membership.
        acme = TenantContext("acme", "alice", "seed-acme")
        other = TenantContext("other", "bob", "seed-other")
        with tenant_scope(acme):
            documents.put("refund-policy", {"text": "Acme refunds within 30 days."},
                          [0.99, 0.01, 0.0], metadata={"topic": "refunds"})
        with tenant_scope(other):
            # A foreign document has an even better similarity score and the
            # same logical ID. Neither similarity nor IDs grant access.
            documents.put("refund-policy", {"text": "Other refunds within 90 days."},
                          [1.0, 0.0, 0.0], metadata={"topic": "refunds"})

        query_vector = [1.0, 0.0, 0.0]  # "What is our refund policy?"
        with tenant_scope(TenantContext("acme", "alice", "query-acme")):
            answer = answer_question(documents, query_vector)
            assert "30 days" in answer and "90 days" not in answer
            print("Acme answer:", answer)

            injected = Or((Eq("tenant_id", "other"), Eq("topic", "refunds")))
            guarded = answer_question(documents, query_vector, predicate=injected)
            assert guarded == answer
            print("Widening selector: tenant boundary preserved.")

            denied = answer_question(documents, query_vector,
                                     predicate=Eq("tenant_id", "other"))
            assert denied == "No accessible documents."
            print("Explicit foreign selector:", denied)

        with tenant_scope(TenantContext("other", "bob", "query-other")):
            answer = answer_question(documents, query_vector)
            assert "90 days" in answer and "30 days" not in answer
            print("Other answer:", answer)
    finally:
        client.close()


if __name__ == "__main__":
    main()
