"""PostgreSQL storage with explicit ownership predicates and transactions."""
import json
import psycopg
from ..exceptions import AdapterError, GuardError, IsolationViolationError
from ..operations import OperationKind
from ..predicates import matches
from .sql import COLUMNS, SCHEMA, compile_selector, row_record


class PostgresAdapter:
    def __init__(self, dsn, *, connect_timeout=5):
        self._dsn = dsn
        self._timeout = connect_timeout

    def _connect(self):
        return psycopg.connect(self._dsn, connect_timeout=self._timeout)

    def initialize(self):
        """Administrative schema setup; runtime roles need only required DML."""
        try:
            with self._connect() as con:
                con.execute(SCHEMA.replace('tenant_id TEXT', 'tenant_id TEXT COLLATE "C"')
                            .replace('record_id TEXT', 'record_id TEXT COLLATE "C"'))
        except Exception:
            raise AdapterError("PostgreSQL schema initialization failed") from None

    def execute(self, operation):
        operation.validate()
        selector, values = compile_selector(operation.selector, "postgres")
        where = "tenant_id = %s AND (" + selector + ")"
        params = [operation.tenant_id, *values]
        try:
            with self._connect() as con:
                if operation.kind is OperationKind.PUT:
                    record = operation.record
                    if not matches(operation.selector, record.fields()):
                        raise IsolationViolationError("Write does not satisfy checked scope")
                    con.execute(
                        "INSERT INTO grd_records (" + COLUMNS + ") VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT(tenant_id, record_id) DO UPDATE SET "
                        "data_json=excluded.data_json, metadata_json=excluded.metadata_json",
                        (record.tenant_id, record.record_id, record.data_json,
                         json.dumps(dict(record.metadata), ensure_ascii=True)))
                    return (record,)
                if operation.kind is OperationKind.DELETE:
                    cursor = con.execute("DELETE FROM grd_records WHERE " + where
                                         + " RETURNING " + COLUMNS, params)
                else:
                    cursor = con.execute("SELECT " + COLUMNS + " FROM grd_records WHERE " + where,
                                         params)
                return tuple(row_record(row) for row in cursor.fetchall())
        except GuardError:
            raise
        except Exception:
            raise AdapterError("PostgreSQL operation failed") from None
