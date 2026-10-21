"""Shared record codec and enforcement for backend adapters."""
from ..context import current_context
from ..exceptions import IsolationViolationError, VerificationError
from ..operations import Operation, OperationKind, Record
from ..predicates import And, Eq, matches, validate_predicate


def payload(record):
    return {"tenant_id": record.tenant_id, "record_id": record.record_id,
            "data_json": record.data_json, "metadata": dict(record.metadata)}


def decode_record(value):
    try:
        if type(value) is not dict or set(value) != {"tenant_id", "record_id", "data_json", "metadata"}:
            raise ValueError()
        if type(value["metadata"]) is not dict:
            raise ValueError()
        return Record(value["tenant_id"], value["record_id"], value["data_json"],
                      tuple(value["metadata"].items()))
    except Exception:
        raise IsolationViolationError("Backend returned an invalid ownership envelope") from None


def authorize(context, operation, engine):
    result = engine.verify_operation(context, operation)
    result.require_proven()
    if (result.operation_digest != operation.digest
            or result.policy_version != context.policy_version
            or current_context() != context):
        raise VerificationError("Operation evidence or active context changed")


def scoped_operation(context, predicate=None, *, kind=OperationKind.SEARCH,
                     record_id=None, record=None):
    terms = [Eq("tenant_id", context.tenant_id)]
    if predicate is not None:
        validate_predicate(predicate)
        terms.append(predicate)
    if record_id is not None:
        terms.append(Eq("record_id", record_id))
    return Operation(context.tenant_id, kind, And(tuple(terms)), record_id, record)


def checked_records(context, operation, records, engine):
    result = tuple(records)
    for record in result:
        engine.verify_ownership(context, record.tenant_id)
        if not matches(operation.selector, record.fields()):
            raise IsolationViolationError("Backend result is outside the verified selector")
    if current_context() != context:
        raise VerificationError("Active context changed during backend execution")
    return result
