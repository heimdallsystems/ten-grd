"""Durable SQLite storage with atomic tenant-scoped mutations."""
import json
import sqlite3
from threading import RLock
from ..exceptions import AdapterError, GuardError, IsolationViolationError
from ..operations import OperationKind
from ..predicates import matches
from .sql import COLUMNS, SCHEMA, compile_selector, row_record


class SQLiteAdapter:
    def __init__(self, path, *, timeout=5.0):
        self._lock = RLock()
        self._connection = sqlite3.connect(str(path), timeout=timeout,
                                           isolation_level=None, check_same_thread=False)
        self._connection.execute(SCHEMA)
        self._connection.execute("SELECT json_extract('{}', '$.field')")

    def close(self):
        with self._lock:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, operation):
        operation.validate()
        selector, values = compile_selector(operation.selector)
        where = "tenant_id = ? AND (" + selector + ")"
        params = [operation.tenant_id, *values]
        with self._lock:
            con = self._connection
            try:
                con.execute("BEGIN IMMEDIATE" if operation.kind in (
                    OperationKind.PUT, OperationKind.DELETE) else "BEGIN")
                if operation.kind is OperationKind.PUT:
                    record = operation.record
                    if not matches(operation.selector, record.fields()):
                        raise IsolationViolationError("Write does not satisfy checked scope")
                    con.execute(
                        "INSERT INTO grd_records (" + COLUMNS + ") VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(tenant_id, record_id) DO UPDATE SET "
                        "data_json=excluded.data_json, metadata_json=excluded.metadata_json",
                        (record.tenant_id, record.record_id, record.data_json,
                         json.dumps(dict(record.metadata), ensure_ascii=True)))
                    records = (record,)
                else:
                    rows = con.execute("SELECT " + COLUMNS + " FROM grd_records WHERE " + where,
                                       params).fetchall()
                    records = tuple(row_record(row) for row in rows)
                    if operation.kind is OperationKind.DELETE:
                        con.execute("DELETE FROM grd_records WHERE " + where, params)
                con.execute("COMMIT")
                return records
            except Exception as exc:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                if isinstance(exc, GuardError):
                    raise
                raise AdapterError("SQLite operation failed") from None
