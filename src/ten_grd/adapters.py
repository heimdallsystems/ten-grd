"""Trusted adapter contract and atomic, in-memory reference implementation."""
from threading import RLock
from typing import Protocol

from .exceptions import InvalidOperationError, IsolationViolationError
from .operations import Operation, OperationKind, Record
from .predicates import matches


class IsolationAdapter(Protocol):
    """Implementations must preserve the exact checked operation semantics.

    An adapter is trusted enforcement code, not an untrusted plugin. Backend
    implementations must bind ownership atomically and document model fidelity.
    """
    def execute(self, operation: Operation) -> tuple[Record, ...]: ...


class InMemoryAdapter:
    """Reference metadata semantics; intentionally not a persistent backend."""

    def __init__(self):
        self._records: dict[tuple[str, str], Record] = {}
        self._lock = RLock()

    def execute(self, operation: Operation) -> tuple[Record, ...]:
        if type(operation) is not Operation:
            raise InvalidOperationError("Expected an Operation")
        operation.validate()
        with self._lock:
            if operation.kind is OperationKind.SEARCH:
                return tuple(record for record in self._records.values()
                             if record.tenant_id == operation.tenant_id
                             and matches(operation.selector, record.fields()))
            key = (operation.tenant_id, operation.record_id)
            if operation.kind is OperationKind.PUT:
                record = operation.record
                if (record.tenant_id != operation.tenant_id
                        or not matches(operation.selector, record.fields())):
                    raise IsolationViolationError("Write does not satisfy its checked scope")
                self._records[key] = record
                return (record,)
            record = self._records.get(key)
            if record is None or not matches(operation.selector, record.fields()):
                return ()
            if operation.kind is OperationKind.DELETE:
                del self._records[key]
            return (record,)
