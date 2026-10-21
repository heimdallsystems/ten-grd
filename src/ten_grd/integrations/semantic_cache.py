"""Semantic cache: hard authorization scope precedes similarity selection."""
import json
import math
import time
from ..context import current_context, validate_identifier
from ..engine import Z3InvariantEngine
from ..exceptions import InvalidOperationError, IsolationViolationError, VerificationError
from ..operations import _json
from .cache import CacheHit, active_scope, cache_key, request_json, validate_ttl


class SemanticCache:
    def __init__(self, retriever, *, namespace, model_version, clock=time.time, engine=None):
        validate_identifier(namespace, "namespace")
        validate_identifier(model_version, "model_version")
        self.retriever, self.namespace, self.model_version = retriever, namespace, model_version
        self.clock = clock
        self.engine = engine if engine is not None else Z3InvariantEngine()

    def set(self, request, vector, data, *, ttl_seconds=300):
        validate_ttl(ttl_seconds)
        context, scope = active_scope(self.namespace, self.model_version, self.engine)
        encoded = request_json(request)
        record_id = cache_key(scope, encoded).split(":")[-1]
        envelope = {"scope": scope.fields(), "request_json": encoded,
                    "value_json": _json(data), "expires_at": self.clock() + ttl_seconds}
        metadata = {k: v for k, v in scope.fields().items() if k != "tenant_id"}
        if current_context() != context:
            raise VerificationError("Active context changed before semantic write")
        self.retriever.put(record_id, envelope, vector, metadata=metadata)

    def get(self, vector, *, min_score=0.9, limit=10):
        if (type(min_score) not in (float, int) or not math.isfinite(min_score)
                or not -1 <= min_score <= 1):
            raise InvalidOperationError("Cosine threshold must be between -1 and 1")
        context, scope = active_scope(self.namespace, self.model_version, self.engine)
        hits = self.retriever.search(vector, predicate=scope.selector(), limit=limit)
        result = None
        # Validate every candidate before exposing any candidate.
        for hit in hits:
            envelope = hit.record.data
            try:
                if (hit.record.tenant_id != scope.tenant_id
                        or any(dict(hit.record.metadata).get(k) != v
                               for k, v in scope.fields().items() if k != "tenant_id")
                        or type(envelope) is not dict or envelope["scope"] != scope.fields()
                        or type(envelope["expires_at"]) not in (float, int)
                        or not math.isfinite(envelope["expires_at"])
                        or not isinstance(envelope["request_json"], str)
                        or cache_key(scope, envelope["request_json"]).split(":")[-1] != hit.record.record_id):
                    raise ValueError()
                value_json = _json(json.loads(envelope["value_json"]))
            except Exception:
                raise IsolationViolationError("Semantic hit has invalid ownership or provenance") from None
            if (result is None and hit.score >= min_score
                    and envelope["expires_at"] > self.clock()):
                result = CacheHit(value_json)
        if current_context() != context:
            raise VerificationError("Active context changed during semantic lookup")
        return result

    def delete(self, request):
        context, scope = active_scope(self.namespace, self.model_version, self.engine)
        record_id = cache_key(scope, request_json(request)).split(":")[-1]
        if current_context() != context:
            raise VerificationError("Active context changed before semantic invalidation")
        self.retriever.delete(record_id)
