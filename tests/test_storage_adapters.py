import random
import uuid
import os
import pytest
from ten_grd import And, Eq, Not, Or, TenantContext, TenantGuard, tenant_scope
from ten_grd.predicates import matches
from ten_grd.integrations.sqlite import SQLiteAdapter
from ten_grd.integrations.sql import compile_selector


@pytest.fixture(params=["sqlite", "postgres"])
def storage(request, tmp_path):
    if request.param == "sqlite":
        adapter = SQLiteAdapter(tmp_path / "records.sqlite")
    else:
        dsn = os.getenv("GRD_POSTGRES_DSN")
        if not dsn:
            if os.getenv("GRD_REQUIRE_SERVICES"):
                pytest.fail("Required PostgreSQL service is not configured")
            pytest.skip("GRD_POSTGRES_DSN not configured")
        from ten_grd.integrations.postgres import PostgresAdapter
        adapter = PostgresAdapter(dsn)
        adapter.initialize()
    suffix = uuid.uuid4().hex
    a = TenantContext("a-" + suffix, "alice", "request-a")
    b = TenantContext("b-" + suffix, "bob", "request-b")
    yield TenantGuard(adapter), a, b
    if hasattr(adapter, "close"):
        adapter.close()


def test_atomic_storage_partitioning(storage):
    guard, a, b = storage
    with tenant_scope(a):
        guard.put("same", {"owner": "a"})
    with tenant_scope(b):
        assert guard.get("same") is None
        assert not guard.delete("same")
        guard.put("same", {"owner": "b"})
    with tenant_scope(a):
        assert guard.get("same").data == {"owner": "a"}
        guard.put("same", {"owner": "a2"})
        assert guard.delete("same")
    with tenant_scope(b):
        assert guard.get("same").data == {"owner": "b"}


def test_sql_literals_and_scope_injection(storage):
    guard, a, b = storage
    literal = "x' OR tenant_id != 'a"
    with tenant_scope(b):
        guard.put("foreign", "private", metadata={"topic": literal})
    with tenant_scope(a):
        own = guard.put("own", "allowed", metadata={"topic": literal})
        predicate = Or((Eq("tenant_id", b.tenant_id), Eq("topic", literal)))
        assert guard.search(predicate) == (own,)
        assert guard.search(Eq("tenant_id", b.tenant_id)) == ()


def test_real_sql_matches_generated_predicate_model(storage):
    guard, a, b = storage
    with tenant_scope(a):
        records = [guard.put(str(i), i, metadata=metadata)
                   for i, metadata in enumerate([{}, {"topic": "x"}, {"topic": "y"},
                                                   {"topic": "x", "kind": "z"}])]
        rng = random.Random(2026)
        atoms = [Eq("topic", "x"), Eq("kind", "z"), Eq("missing", "x")]
        def generate(depth):
            if depth == 0:
                return rng.choice(atoms)
            cls = rng.choice([And, Or, Not])
            return Not(generate(depth - 1)) if cls is Not else cls(
                (generate(depth - 1), generate(depth - 1)))
        selectors = [And(()), Or(()), Not(Eq("missing", "x"))]
        selectors += [generate(3) for _ in range(30)]
        for predicate in selectors:
            expected = {r.record_id for r in records if matches(predicate, r.fields())}
            actual = {r.record_id for r in guard.search(predicate)}
            assert actual == expected, predicate


def test_sqlite_survives_restart(tmp_path):
    path = tmp_path / "persistent.sqlite"
    context = TenantContext("tenant", "user", "request")
    with tenant_scope(context):
        with SQLiteAdapter(path) as adapter:
            TenantGuard(adapter).put("saved", {"persisted": True})
        with SQLiteAdapter(path) as adapter:
            assert TenantGuard(adapter).get("saved").data == {"persisted": True}


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
def test_sql_compiler_binds_values(dialect):
    literal = "'; DROP TABLE grd_records; --"
    sql, params = compile_selector(Eq("topic", literal), dialect)
    assert literal not in sql
    assert literal in params


def test_sqlite_rejects_corrupt_record_before_delete(tmp_path):
    from ten_grd import IsolationViolationError
    with SQLiteAdapter(tmp_path / "corrupt.sqlite") as adapter:
        with tenant_scope(TenantContext("tenant", "user", "request")):
            guard = TenantGuard(adapter)
            guard.put("item", "data")
            adapter._connection.execute("UPDATE grd_records SET metadata_json = '[]'")
            with pytest.raises(IsolationViolationError):
                guard.delete("item")
            assert adapter._connection.execute("SELECT count(*) FROM grd_records").fetchone()[0] == 1
