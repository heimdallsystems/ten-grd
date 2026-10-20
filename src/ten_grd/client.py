"""Guarded operations for agent tools, memory, and application data."""
from .context import current_context
from .engine import Z3InvariantEngine
from .exceptions import InvalidOperationError, IsolationViolationError, VerificationError
from .operations import Operation, OperationKind, Record
from .predicates import And, Eq, Predicate, matches, validate_predicate


class TenantGuard:
    def __init__(self, adapter, *, engine=None):
        self._adapter = adapter
        self._engine = engine if engine is not None else Z3InvariantEngine()

    def search(self, predicate: Predicate | None = None) -> tuple[Record, ...]:
        return self._run(OperationKind.SEARCH, predicate=predicate)

    def get(self, record_id: str) -> Record | None:
        records = self._run(OperationKind.GET, record_id=record_id)
        return records[0] if records else None

    def put(self, record_id: str, data, *, metadata=None) -> Record:
        context = current_context()
        record = Record.create(context.tenant_id, record_id, data, metadata)
        records = self._run(OperationKind.PUT, record_id=record_id, record=record)
        if len(records) != 1 or records[0] != record:
            raise VerificationError("Adapter did not return the exact written record")
        return records[0]

    def delete(self, record_id: str) -> bool:
        return bool(self._run(OperationKind.DELETE, record_id=record_id))

    def _run(self, kind, *, predicate=None, record_id=None, record=None):
        context = current_context()
        terms = [Eq("tenant_id", context.tenant_id)]
        if predicate is not None:
            validate_predicate(predicate)
            terms.append(predicate)
        if record_id is not None:
            terms.append(Eq("record_id", record_id))
        operation = Operation(context.tenant_id, kind, And(tuple(terms)),
                              record_id, record)
        result = self._engine.verify_operation(context, operation)
        result.require_proven()
        if (result.operation_digest != operation.digest
                or result.policy_version != context.policy_version):
            raise VerificationError("Verification evidence does not match the operation")
        if current_context() != context:
            raise VerificationError("Tenant context changed during verification")
        records = self._adapter.execute(operation)
        # Materialize and validate the entire batch before returning any content.
        if type(records) is not tuple:
            raise InvalidOperationError("Adapter must return a materialized record tuple")
        if kind is not OperationKind.SEARCH and len(records) > 1:
            raise VerificationError("Adapter returned multiple records for one target")
        for item in records:
            if type(item) is not Record:
                raise IsolationViolationError("Adapter returned an unsupported or unlabeled record")
            self._engine.verify_ownership(context, item.tenant_id)
            if not matches(operation.selector, item.fields()):
                raise IsolationViolationError("Adapter result falls outside the checked selector")
        return records
