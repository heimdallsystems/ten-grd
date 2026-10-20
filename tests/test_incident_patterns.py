"""Incident-inspired regressions; not Spring AI or LiteLLM exploit reproductions."""
import pytest

from ten_grd import (
    Eq, InMemoryAdapter, InvalidOperationError, IsolationViolationError,
    Or, TenantContext, TenantGuard, tenant_scope,
)
from ten_grd.operations import Record

A = TenantContext("tenant-a", "alice", "request-a")
B = TenantContext("tenant-b", "bob", "request-b")


@pytest.mark.parametrize("literal", [
    'x" || tenant_id == "tenant-b',
    "x' OR tenant_id = 'tenant-b",
    'x\\"] || @.tenant_id == "tenant-b',
])
def test_query_syntax_in_value_remains_literal(literal):
    guard = TenantGuard(InMemoryAdapter())
    with tenant_scope(B):
        guard.put("foreign", "private", metadata={"topic": literal})
    with tenant_scope(A):
        guard.put("own", "safe", metadata={"topic": literal})
        assert [r.record_id for r in guard.search(Eq("topic", literal))] == ["own"]
        assert guard.search(Eq("topic", literal + "x")) == ()


def test_injected_field_name_is_rejected_before_adapter_execution():
    class NeverExecute:
        def execute(self, operation):
            pytest.fail("invalid field must not reach adapter")
    with tenant_scope(A), pytest.raises(InvalidOperationError):
        TenantGuard(NeverExecute()).search(Eq('topic"] || tenant_id', "x"))


def test_disjunctive_tenant_scope_cannot_override_guard():
    guard = TenantGuard(InMemoryAdapter())
    with tenant_scope(B):
        guard.put("foreign", "private")
    with tenant_scope(A):
        guard.put("own", "safe")
        selector = Or((Eq("tenant_id", A.tenant_id), Eq("tenant_id", B.tenant_id)))
        assert [r.record_id for r in guard.search(selector)] == ["own"]


def test_foreign_tool_directives_never_reach_caller():
    # Simulates a foreign result from an adapter. This is NOT a cache adapter
    # test: it exercises the generic output boundary and caller control flow.
    foreign = Record.create(B.tenant_id, "completion", {
        "tool_calls": [{"name": "export_data", "arguments": {"target": "foreign"}}],
    })
    class LeakyAdapter:
        def execute(self, operation):
            return (foreign,)
    received = []
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        records = TenantGuard(LeakyAdapter()).search()
        received.extend(record.data for record in records)
    assert received == []
