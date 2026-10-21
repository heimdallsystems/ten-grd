"""Dense-vector retrieval with structural filters and ownership checks."""
from dataclasses import dataclass
import json
import math
import uuid
from qdrant_client import models
from ..context import current_context
from ..engine import Z3InvariantEngine
from ..exceptions import AdapterError, GuardError, InvalidOperationError, IsolationViolationError
from ..operations import OperationKind, Record
from ..predicates import And, Eq, Or, validate_predicate
from ._shared import authorize, checked_records, decode_record, payload, scoped_operation


@dataclass(frozen=True)
class RetrievalHit:
    record: Record
    score: float


def compile_filter(selector):
    validate_predicate(selector)
    def visit(node):
        if type(node) is Eq:
            key = node.field if node.field in ("tenant_id", "record_id") else "metadata." + node.field
            return models.FieldCondition(key=key, match=models.MatchValue(value=node.value))
        if type(node) is And:
            return models.Filter(must=[visit(p) for p in node.terms])
        if type(node) is Or:
            if node.terms:
                return models.Filter(should=[visit(p) for p in node.terms])
            atom = models.FieldCondition(key="tenant_id", match=models.MatchValue(value=""))
            return models.Filter(must=[atom], must_not=[atom])
        return models.Filter(must_not=[visit(node.term)])
    condition = visit(selector)
    return condition if isinstance(condition, models.Filter) else models.Filter(must=[condition])


def point_id(tenant_id, record_id):
    encoded = json.dumps([tenant_id, record_id], ensure_ascii=True, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ten-grd:v1:" + encoded))


class QdrantRetriever:
    def __init__(self, client, collection, dimension, *, create=False, engine=None):
        if not isinstance(collection, str) or not collection or len(collection) > 128:
            raise InvalidOperationError("A collection name is required")
        if type(dimension) is not int or not 1 <= dimension <= 65536:
            raise InvalidOperationError("Invalid vector dimension")
        self.client, self.collection, self.dimension = client, collection, dimension
        self.engine = engine if engine is not None else Z3InvariantEngine()
        try:
            if create and not client.collection_exists(collection):
                client.create_collection(collection, vectors_config=models.VectorParams(
                    size=dimension, distance=models.Distance.COSINE))
            config = client.get_collection(collection).config.params.vectors
            if not isinstance(config, models.VectorParams) or config.size != dimension:
                raise InvalidOperationError("Expected one dense vector with matching dimension")
            if config.distance != models.Distance.COSINE:
                raise InvalidOperationError("Only cosine collections are supported")
        except GuardError:
            raise
        except Exception:
            raise AdapterError("Qdrant collection initialization failed") from None

    def _vector(self, vector):
        try:
            result = tuple(vector)
            if len(result) != self.dimension or any(
                type(v) not in (float, int) or not math.isfinite(v) for v in result
            ):
                raise ValueError()
            return tuple(float(v) for v in result)
        except Exception:
            raise InvalidOperationError("Expected a finite vector of the configured dimension") from None

    def put(self, record_id, data, vector, *, metadata=None):
        context = current_context()
        record = Record.create(context.tenant_id, record_id, data, metadata)
        operation = scoped_operation(context, kind=OperationKind.PUT,
                                     record_id=record_id, record=record)
        values = self._vector(vector)
        authorize(context, operation, self.engine)
        try:
            self.client.upsert(self.collection, points=[models.PointStruct(
                id=point_id(context.tenant_id, record_id), vector=list(values),
                payload=payload(record))], wait=True)
        except Exception:
            raise AdapterError("Qdrant write failed") from None
        return record

    def search(self, vector, *, predicate=None, limit=10):
        context = current_context()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise InvalidOperationError("limit must be between 1 and 1000")
        values = self._vector(vector)
        operation = scoped_operation(context, predicate)
        backend_filter = compile_filter(operation.selector)
        authorize(context, operation, self.engine)
        try:
            points = self.client.query_points(
                self.collection, query=list(values), query_filter=backend_filter,
                limit=limit, with_payload=True, with_vectors=False).points
        except Exception:
            raise AdapterError("Qdrant query failed") from None
        hits = []
        for point in points:
            record = decode_record(point.payload)
            if (str(point.id) != point_id(record.tenant_id, record.record_id)
                    or not math.isfinite(point.score)):
                raise IsolationViolationError("Qdrant returned an invalid record binding")
            hits.append(RetrievalHit(record, float(point.score)))
        checked_records(context, operation, (hit.record for hit in hits), self.engine)
        return tuple(hits)

    def delete(self, record_id):
        context = current_context()
        operation = scoped_operation(context, kind=OperationKind.DELETE, record_id=record_id)
        authorize(context, operation, self.engine)
        try:
            self.client.delete(self.collection, points_selector=models.FilterSelector(
                filter=compile_filter(operation.selector)), wait=True)
        except Exception:
            raise AdapterError("Qdrant deletion failed") from None
