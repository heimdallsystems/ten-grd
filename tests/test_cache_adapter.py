from dataclasses import replace
import json
import os
import uuid
import pytest
import fakeredis
from ten_grd import (
    Eq, TenantContext, tenant_scope, VerificationStatus, Z3InvariantEngine,
    IsolationViolationError, InvalidContextError, VerificationError,
)
from ten_grd.engine import VerificationResult
from ten_grd.integrations.cache import RedisCache, CacheScope, cache_key, request_json

A = TenantContext("tenant-a", "alice", "request-a")
B = TenantContext("tenant-b", "bob", "request-b")


@pytest.fixture(params=["fake", "server"])
def cache(request):
    if request.param == "fake":
        client = fakeredis.FakeRedis()
    else:
        url = os.getenv("GRD_REDIS_URL")
        if not url:
            if os.getenv("GRD_REQUIRE_SERVICES"):
                pytest.fail("Required Redis service is not configured")
            pytest.skip("GRD_REDIS_URL not configured")
        import redis
        client = redis.Redis.from_url(url, socket_timeout=5, socket_connect_timeout=5)
        client.ping()
    yield RedisCache(client, namespace="test-" + uuid.uuid4().hex, model_version="v1")
    client.close()


def key_for(cache, context, request):
    scope = CacheScope(context.tenant_id, context.subject_id, context.policy_version,
                       cache.namespace, cache.model_version)
    return cache_key(scope, request_json(request))


def test_identical_requests_never_cross_tenants(cache):
    request = {"prompt": "same", "model": "model"}
    with tenant_scope(A):
        cache.set(request, {"private": "a"})
    with tenant_scope(B):
        assert cache.get(request) is None
        assert cache.delete(request) is False
        cache.set(request, {"private": "b"})
    with tenant_scope(A):
        assert cache.get(request).data == {"private": "a"}
        assert cache.delete(request)
    with tenant_scope(B):
        assert cache.get(request).data == {"private": "b"}


def test_subject_policy_and_model_scope_are_bound(cache):
    with tenant_scope(A):
        cache.set("prompt", "private")
        assert cache.get("prompt").data == "private"
    for context in [replace(A, subject_id="other"), replace(A, policy_version="2")]:
        with tenant_scope(context):
            assert cache.get("prompt") is None
    with tenant_scope(A):
        other = RedisCache(cache.client, namespace=cache.namespace, model_version="v2")
        assert other.get("prompt") is None


def test_poisoned_foreign_envelope_is_denied(cache):
    with tenant_scope(B):
        cache.set("prompt", {"tool_calls": [{"name": "export"}]})
    raw = cache.client.get(key_for(cache, B, "prompt"))
    cache.client.set(key_for(cache, A, "prompt"), raw)
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        cache.get("prompt")


def test_wrong_request_binding_is_denied(cache):
    with tenant_scope(A):
        cache.set("first", "private")
    raw = cache.client.get(key_for(cache, A, "first"))
    cache.client.set(key_for(cache, A, "second"), raw)
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        cache.get("second")


def test_ttl_and_cached_null_are_distinct_from_miss(cache):
    with tenant_scope(A):
        cache.set("null", None, ttl_seconds=30)
        assert cache.get("null").data is None
        assert 0 < cache.client.ttl(key_for(cache, A, "null")) <= 30
        cache.client.expire(key_for(cache, A, "null"), 0)
        assert cache.get("null") is None


def test_missing_identity_never_looks_up_cache(cache):
    with pytest.raises(InvalidContextError):
        cache.get("prompt")


def test_symbolic_scope_check_detects_missing_subject_and_policy():
    engine = Z3InvariantEngine()
    result = engine.verify_scope(A, Eq("tenant_id", A.tenant_id), required={})
    assert result.status is VerificationStatus.VIOLATION
    scope = CacheScope(A.tenant_id, A.subject_id, A.policy_version, "memory", "v1")
    assert engine.verify_scope(A, scope.selector(), required=scope.fields()).status is VerificationStatus.PROVEN
    conflict = dict(scope.fields(), subject_id="other")
    assert engine.verify_scope(A, scope.selector(), required=conflict).status is VerificationStatus.VIOLATION


def test_inconclusive_cache_proof_never_writes():
    class Inconclusive(Z3InvariantEngine):
        def verify_scope(self, *args, **kwargs):
            return VerificationResult(VerificationStatus.UNKNOWN, "", "", "timeout")
    client = fakeredis.FakeRedis()
    guarded = RedisCache(client, namespace="test", model_version="v1", engine=Inconclusive())
    with tenant_scope(A), pytest.raises(VerificationError):
        guarded.set("request", "private")
    assert client.dbsize() == 0
    client.close()


def test_structured_keys_do_not_confuse_delimiters():
    left = CacheScope("a:b", "c", "1", "test", "v1")
    right = CacheScope("a", "b:c", "1", "test", "v1")
    assert cache_key(left, request_json("x")) != cache_key(right, request_json("x"))
