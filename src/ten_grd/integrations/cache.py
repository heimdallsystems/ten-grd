"""Exact cache scopes bind tenant, subject, policy and configuration."""
from dataclasses import dataclass
import hashlib
import json

from ..context import current_context, validate_identifier
from ..engine import Z3InvariantEngine
from ..exceptions import AdapterError, InvalidOperationError, IsolationViolationError, VerificationError
from ..operations import _json
from ..predicates import And, Eq


@dataclass(frozen=True)
class CacheScope:
    tenant_id: str
    subject_id: str
    policy_version: str
    namespace: str
    model_version: str

    def fields(self):
        return dict(tenant_id=self.tenant_id, subject_id=self.subject_id,
                    policy_version=self.policy_version, namespace=self.namespace,
                    model_version=self.model_version)

    def selector(self):
        return And(tuple(Eq(k, v) for k, v in self.fields().items()))


@dataclass(frozen=True)
class CacheHit:
    value_json: str

    @property
    def data(self):
        return json.loads(self.value_json)


def active_scope(namespace, model_version, engine):
    context = current_context()
    scope = CacheScope(context.tenant_id, context.subject_id, context.policy_version,
                       namespace, model_version)
    engine.verify_scope(context, scope.selector(), required=scope.fields()).require_proven()
    if current_context() != context:
        raise VerificationError("Active context changed during cache verification")
    return context, scope


def request_json(request):
    encoded = _json(request)
    if len(encoded) > 65536:
        raise InvalidOperationError("Cache request exceeds 64 KiB")
    return encoded


def cache_key(scope, encoded_request):
    # Hashing is NOT modeled as injective. Reads validate the full binding.
    canonical = _json(["ten-grd-cache-v1", scope.fields(), encoded_request])
    return "ten-grd:cache:v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_ttl(ttl_seconds):
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 31_536_000:
        raise InvalidOperationError("TTL must be an integer between 1 and 31536000 seconds")


class RedisCache:
    """Synchronous redis-py client or compatible backend; no broad-scope fallback."""
    def __init__(self, client, *, namespace, model_version, engine=None):
        validate_identifier(namespace, "namespace")
        validate_identifier(model_version, "model_version")
        self.client, self.namespace, self.model_version = client, namespace, model_version
        self.engine = engine if engine is not None else Z3InvariantEngine()

    def _binding(self, request):
        context, scope = active_scope(self.namespace, self.model_version, self.engine)
        encoded = request_json(request)
        return context, scope, encoded, cache_key(scope, encoded)

    def set(self, request, data, *, ttl_seconds=300):
        validate_ttl(ttl_seconds)
        context, scope, encoded, key = self._binding(request)
        value_json = _json(data)
        if len(value_json) > 1_048_576:
            raise InvalidOperationError("Cached value exceeds 1 MiB")
        envelope = _json({"scope": scope.fields(), "request_json": encoded,
                          "value_json": value_json})
        if current_context() != context:
            raise VerificationError("Active context changed before cache write")
        try:
            written = self.client.set(key, envelope, ex=ttl_seconds)
        except Exception:
            raise AdapterError("Redis cache write failed") from None
        if not written:
            raise AdapterError("Redis did not acknowledge the cache write")

    def get(self, request):
        context, scope, encoded, key = self._binding(request)
        try:
            raw = self.client.get(key)
        except Exception:
            raise AdapterError("Redis cache lookup failed") from None
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
            if (type(envelope) is not dict or envelope["scope"] != scope.fields()
                    or envelope["request_json"] != encoded
                    or not isinstance(envelope["value_json"], str)):
                raise ValueError()
            value = json.loads(envelope["value_json"])
            canonical = _json(value)
            if len(canonical) > 1_048_576:
                raise ValueError()
        except Exception:
            raise IsolationViolationError("Cache hit has invalid ownership or request binding") from None
        if current_context() != context:
            raise VerificationError("Active context changed during cache lookup")
        return CacheHit(canonical)

    def delete(self, request):
        _, _, _, key = self._binding(request)
        try:
            return bool(self.client.delete(key))
        except Exception:
            raise AdapterError("Redis cache invalidation failed") from None
