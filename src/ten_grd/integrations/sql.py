"""Parameter-bound SQL compilation for the string-metadata model."""
import json
from ..exceptions import IsolationViolationError
from ..operations import Record
from ..predicates import And, Eq, Or, validate_predicate

SCHEMA = """CREATE TABLE IF NOT EXISTS grd_records (
    tenant_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, record_id)
)"""
COLUMNS = "tenant_id, record_id, data_json, metadata_json"


def compile_selector(selector, dialect="sqlite"):
    validate_predicate(selector)
    if dialect not in ("sqlite", "postgres"):
        raise ValueError("Unsupported SQL dialect")
    marker = "?" if dialect == "sqlite" else "%s"
    params = []
    def visit(node):
        if type(node) is Eq:
            if node.field in ("tenant_id", "record_id"):
                column = node.field
            elif dialect == "sqlite":
                column = f"json_extract(metadata_json, {marker})"
                params.append("$." + node.field)
            else:
                column = f"(metadata_json::jsonb ->> {marker})"
                params.append(node.field)
            params.append(node.value)
            collation = 'BINARY' if dialect == 'sqlite' else '"C"'
            return f"COALESCE(({column} COLLATE {collation}) = {marker}, FALSE)"
        if type(node) in (And, Or):
            if not node.terms:
                return "TRUE" if type(node) is And else "FALSE"
            joiner = " AND " if type(node) is And else " OR "
            return "(" + joiner.join(visit(term) for term in node.terms) + ")"
        return "(NOT " + visit(node.term) + ")"
    return visit(selector), params


def row_record(row):
    try:
        tenant, record_id, data_json, metadata_json = row
        metadata = json.loads(metadata_json)
        if type(metadata) is not dict:
            raise ValueError()
        return Record(tenant, record_id, data_json, tuple(metadata.items()))
    except Exception:
        raise IsolationViolationError("Stored record has invalid ownership or metadata") from None
