from dataclasses import replace
import os
import random
import json
import uuid
from types import SimpleNamespace
import pytest

pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient, models
from ten_grd import And, Eq, Not, Or, TenantContext, tenant_scope, IsolationViolationError
from ten_grd.predicates import matches
from ten_grd.integrations.qdrant import QdrantRetriever, point_id
from ten_grd.integrations.semantic_cache import SemanticCache
from ten_grd.integrations.cache import CacheScope, cache_key
from ten_grd.integrations._shared import payload
from ten_grd.operations import Record

A = TenantContext("tenant-a", "alice", "request-a")
B = TenantContext("tenant-b", "bob", "request-b")


@pytest.fixture(params=["local", "server"])
def retrieval(request):
    if request.param == "local":
        client = QdrantClient(location=":memory:", force_disable_check_same_thread=True)
    else:
        url = os.getenv("GRD_QDRANT_URL")
        if not url:
            if os.getenv("GRD_REQUIRE_SERVICES"):
                pytest.fail("Required Qdrant service is not configured")
            pytest.skip("GRD_QDRANT_URL not configured")
        client = QdrantClient(url=url, timeout=10)
    collection = "grd_test_" + uuid.uuid4().hex
    retriever = QdrantRetriever(client, collection, 3, create=True)
    yield retriever
    client.delete_collection(collection)
    client.close()


def test_vector_similarity_cannot_cross_tenant_partition(retrieval):
    with tenant_scope(B):
        retrieval.put("same", "private-b", [1, 0, 0])
    with tenant_scope(A):
        retrieval.put("same", "private-a", [0.9, 0.1, 0])
        hits = retrieval.search([1, 0, 0])
        assert [hit.record.data for hit in hits] == ["private-a"]
        retrieval.delete("same")
        assert retrieval.search([1, 0, 0]) == ()
    with tenant_scope(B):
        assert retrieval.search([1, 0, 0])[0].record.data == "private-b"


def test_real_vector_backend_matches_selector_model(retrieval):
    with tenant_scope(A):
        records = [retrieval.put(str(i), i, [1, 0, 0], metadata=meta)
                   for i, meta in enumerate([{}, {"topic": "x"}, {"topic": "y"},
                                              {"topic": 'x" || tenant_id != "a'}])]
        rng = random.Random(10)
        atoms = [Eq("topic", "x"), Eq("missing", "x"),
                 Eq("topic", 'x" || tenant_id != "a')]
        def gen(depth):
            if depth == 0:
                return rng.choice(atoms)
            cls = rng.choice([And, Or, Not])
            return Not(gen(depth - 1)) if cls is Not else cls((gen(depth - 1), gen(depth - 1)))
        selectors = [And(()), Or(()), Not(Eq("missing", "x"))]
        selectors += [gen(3) for _ in range(30)]
        for predicate in selectors:
            expected = {r.record_id for r in records if matches(predicate, r.fields())}
            actual = {h.record.record_id for h in retrieval.search([1, 0, 0], predicate=predicate)}
            assert actual == expected, predicate


def test_foreign_payload_from_faulty_backend_rejected(retrieval, monkeypatch):
    foreign = Record.create(B.tenant_id, "secret", {"tool_calls": [{"name": "export"}]})
    point = models.ScoredPoint(id=point_id(B.tenant_id, "secret"), version=1,
                               score=1.0, payload=payload(foreign))
    monkeypatch.setattr(retrieval.client, "query_points",
                        lambda *args, **kwargs: SimpleNamespace(points=[point]))
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        retrieval.search([1, 0, 0])


def test_semantic_cache_scope_precedes_similarity(retrieval):
    cache = SemanticCache(retrieval, namespace="answers", model_version="v1")
    with tenant_scope(A):
        cache.set("question", [1, 0, 0], {"private": "a"})
    with tenant_scope(B):
        assert cache.get([1, 0, 0]) is None
        cache.set("question", [1, 0, 0], {"private": "b"})
    with tenant_scope(replace(A, subject_id="other-user")):
        assert cache.get([1, 0, 0]) is None
    with tenant_scope(replace(A, policy_version="2")):
        assert cache.get([1, 0, 0]) is None
    with tenant_scope(A):
        # A similar request can hit A's response, never the identical foreign vector.
        assert cache.get([0.99, 0.01, 0]).data == {"private": "a"}
        cache.delete("question")
        assert cache.get([1, 0, 0]) is None
    with tenant_scope(B):
        assert cache.get([1, 0, 0]).data == {"private": "b"}


def test_semantic_cache_expiration(retrieval):
    now = [1000.0]
    cache = SemanticCache(retrieval, namespace="ttl", model_version="v1", clock=lambda: now[0])
    with tenant_scope(A):
        cache.set("question", [1, 0, 0], "temporary", ttl_seconds=1)
        assert cache.get([1, 0, 0]).data == "temporary"
        now[0] = 1002.0
        assert cache.get([1, 0, 0]) is None


def test_semantic_cache_validates_entry_scope_not_only_filter(retrieval):
    cache = SemanticCache(retrieval, namespace="answers", model_version="v1")
    with tenant_scope(A):
        cache.set("question", [1, 0, 0], "private")
    pid = point_id(A.tenant_id, cache_key(
        CacheScope(
            A.tenant_id, A.subject_id, A.policy_version, "answers", "v1"),
        '"question"').split(":")[-1])
    stored = retrieval.client.retrieve(retrieval.collection, [pid], with_payload=True)[0]
    data = json.loads(stored.payload["data_json"])
    data["scope"]["subject_id"] = "foreign"
    corrupted = dict(stored.payload, data_json=json.dumps(data))
    retrieval.client.upsert(retrieval.collection,
                           [models.PointStruct(id=pid, vector=[1.0, 0.0, 0.0], payload=corrupted)],
                           wait=True)
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        cache.get([1, 0, 0])
