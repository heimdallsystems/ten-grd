"""Immutable operation snapshots bind verification to execution inputs."""
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from .context import validate_identifier
from .exceptions import InvalidOperationError
from .predicates import Predicate, predicate_data, validate_predicate


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise InvalidOperationError("Payload must be finite JSON data") from exc


@dataclass(frozen=True)
class Record:
    tenant_id: str
    record_id: str
    data_json: str
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        validate_identifier(self.tenant_id, "tenant_id")
        validate_identifier(self.record_id, "record_id")
        if not isinstance(self.data_json, str) or len(self.data_json) > 1_048_576:
            raise InvalidOperationError("Payload must be JSON within 1 MiB")
        try:
            data = json.loads(self.data_json, parse_constant=_reject_constant)
        except (ValueError, TypeError, RecursionError) as exc:
            raise InvalidOperationError("Invalid JSON payload") from exc
        object.__setattr__(self, "data_json", _json(data))
        if type(self.metadata) is not tuple:
            raise InvalidOperationError("Metadata must be an immutable tuple")
        keys = set()
        for item in self.metadata:
            if type(item) is not tuple or len(item) != 2:
                raise InvalidOperationError("Metadata entries must be string pairs")
            key, value = item
            if (not isinstance(key, str) or not key.isidentifier() or len(key) > 128
                    or key in ("tenant_id", "record_id") or key in keys
                    or not isinstance(value, str) or len(value) > 4096):
                raise InvalidOperationError("Invalid or reserved metadata field")
            keys.add(key)
        object.__setattr__(self, "metadata", tuple(sorted(self.metadata)))

    @classmethod
    def create(cls, tenant_id: str, record_id: str, data: Any, metadata=None):
        if metadata is not None and type(metadata) is not dict:
            raise InvalidOperationError("Metadata must be a dictionary of strings")
        return cls(tenant_id, record_id, _json(data),
                   tuple((metadata or {}).items()))

    @property
    def data(self):
        """Return a new value so callers cannot mutate the stored snapshot."""
        return json.loads(self.data_json)

    def fields(self):
        return dict(self.metadata, tenant_id=self.tenant_id, record_id=self.record_id)


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


class OperationKind(str, Enum):
    SEARCH = "search"
    GET = "get"
    PUT = "put"
    DELETE = "delete"


@dataclass(frozen=True)
class Operation:
    tenant_id: str
    kind: OperationKind
    selector: Predicate
    record_id: str | None = None
    record: Record | None = None

    def validate(self):
        validate_identifier(self.tenant_id, "tenant_id")
        if type(self.kind) is not OperationKind:
            raise InvalidOperationError("Unsupported operation kind")
        validate_predicate(self.selector)
        if self.kind is OperationKind.SEARCH:
            if self.record_id is not None or self.record is not None:
                raise InvalidOperationError("Search cannot contain write or target data")
        else:
            validate_identifier(self.record_id, "record_id")
            if self.kind is OperationKind.PUT:
                if (type(self.record) is not Record or self.record.tenant_id != self.tenant_id
                        or self.record.record_id != self.record_id):
                    raise InvalidOperationError("Write ownership must match the operation")
            elif self.record is not None:
                raise InvalidOperationError("Only put can contain a record")

    @property
    def digest(self):
        self.validate()
        record = None if self.record is None else [
            self.record.tenant_id, self.record.record_id,
            self.record.data_json, self.record.metadata,
        ]
        encoded = _json([self.tenant_id, self.kind.value,
                         predicate_data(self.selector), self.record_id, record])
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
