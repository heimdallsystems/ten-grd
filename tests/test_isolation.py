import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import itertools
import random

import pytest
import z3

from ten_grd import (
    And, Eq, InMemoryAdapter, InvalidContextError, InvalidOperationError,
    IsolationViolationError, Not, Or, TenantContext, TenantGuard,
    VerificationError, VerificationStatus, Z3InvariantEngine,
    current_context, tenant_scope,
)
from ten_grd.operations import Record
from ten_grd.predicates import matches

A = TenantContext("tenant-a", "alice", "request-a")
B = TenantContext("tenant-b", "bob", "request-b")


@pytest.fixture
def guard():
    return TenantGuard(InMemoryAdapter())


def test_missing_context_denies_before_backend():
    class Backend:
        def execute(self, operation):
            pytest.fail("must not execute without tenant context")
    with pytest.raises(InvalidContextError):
        TenantGuard(Backend()).get("secret")


@pytest.mark.parametrize("tenant", ["", " ", " tenant-a", "tenant-a ", None, 1, "a\n", "x" * 257])
def test_invalid_identity(tenant):
    with pytest.raises(InvalidContextError):
        TenantContext(tenant, "subject", "request")


def test_nested_scope_and_exception_restore():
    with pytest.raises(InvalidContextError):
        current_context()
    with tenant_scope(A):
        assert current_context() == A
        with pytest.raises(RuntimeError):
            with tenant_scope(B):
                assert current_context() == B
                raise RuntimeError()
        assert current_context() == A
    with pytest.raises(InvalidContextError):
        current_context()


def test_same_identifier_is_partitioned_for_all_operations(guard):
    with tenant_scope(A):
        guard.put("plan", {"private": "a"})
    with tenant_scope(B):
        assert guard.get("plan") is None
        assert guard.delete("plan") is False
        guard.put("plan", {"private": "b"})
        assert guard.get("plan").data == {"private": "b"}
    with tenant_scope(A):
        assert guard.get("plan").data == {"private": "a"}
        assert guard.delete("plan") is True
        assert guard.get("plan") is None
    with tenant_scope(B):
        assert guard.get("plan").data == {"private": "b"}


def test_query_injection_cannot_weaken_owned_scope(guard):
    with tenant_scope(B):
        guard.put("b", "private", metadata={"topic": "shared"})
    with tenant_scope(A):
        guard.put("a", "allowed", metadata={"topic": "shared"})
        injection = Or((Eq("tenant_id", "tenant-b"), Eq("topic", "shared")))
        assert [r.record_id for r in guard.search(injection)] == ["a"]
        assert guard.search(Eq("tenant_id", "tenant-b")) == ()


def test_payload_and_metadata_are_snapshots(guard):
    data = {"messages": ["first"]}
    metadata = {"topic": "safe"}
    with tenant_scope(A):
        record = guard.put("memory", data, metadata=metadata)
        data["messages"].append("mutated")
        metadata["topic"] = "changed"
        record.data["messages"].append("also changed")
        assert guard.get("memory").data == {"messages": ["first"]}
        assert guard.search(Eq("topic", "safe")) == (record,)


@pytest.mark.parametrize("metadata", [{"tenant_id": "tenant-b"}, {"record_id": "other"},
                                      {"bad-field": "value"}, {"topic": 42}])
def test_metadata_cannot_override_reserved_fields(guard, metadata):
    with tenant_scope(A), pytest.raises(InvalidOperationError):
        guard.put("item", {}, metadata=metadata)


@pytest.mark.parametrize("data", [float("nan"), float("inf"), object()])
def test_non_json_payload_rejected(guard, data):
    with tenant_scope(A), pytest.raises(InvalidOperationError):
        guard.put("item", data)


@pytest.mark.parametrize("selector", [
    Eq("tenant_id", "tenant-a"),
    And((Eq("tenant_id", "tenant-a"), Or((Eq("topic", "x"), Not(Eq("topic", "x")))))),
    Or(()),  # Deliberately empty selection is safe, not an authorization grant.
])
def test_proven_selector(selector):
    result = Z3InvariantEngine().verify_selector(A, selector)
    assert result.status is VerificationStatus.PROVEN
    result.require_proven()


@pytest.mark.parametrize("selector", [
    And(()), Eq("topic", "x"), Eq("tenant_id", "tenant-b"),
    Or((Eq("tenant_id", "tenant-a"), Eq("tenant_id", "tenant-b"))),
    Not(Eq("tenant_id", "tenant-b")),
])
def test_unsafe_selector_cannot_prove_isolation(selector):
    result = Z3InvariantEngine().verify_selector(A, selector)
    assert result.status is VerificationStatus.VIOLATION
    with pytest.raises(IsolationViolationError):
        result.require_proven()


@pytest.mark.parametrize("owner", ["tenant-b", "", None])
def test_direct_owner_check_always_denies_foreign_or_unknown(owner):
    with pytest.raises(IsolationViolationError):
        Z3InvariantEngine().verify_ownership(A, owner)


@pytest.mark.parametrize("selector", ["tenant_id = 'tenant-a'", Eq("topic", 1), object()])
def test_unsupported_selectors_fail_closed(selector):
    result = Z3InvariantEngine().verify_selector(A, selector)
    assert result.status is VerificationStatus.UNSUPPORTED
    with pytest.raises(VerificationError):
        result.require_proven()


def test_complexity_bound():
    selector = Eq("tenant_id", "tenant-a")
    for _ in range(40):
        selector = Not(selector)
    result = Z3InvariantEngine().verify_selector(A, selector)
    assert result.status is VerificationStatus.UNSUPPORTED


def test_mutable_ast_terms_rejected():
    with pytest.raises(InvalidOperationError):
        And([Eq("tenant_id", "tenant-a")])


@pytest.mark.parametrize("reason", ["incomplete", "timeout", "canceled"])
def test_unknown_and_timeout_never_execute(monkeypatch, reason):
    class InconclusiveSolver:
        def __init__(self, **kwargs): pass
        def set(self, **kwargs): pass
        def add(self, *args): pass
        def check(self): return z3.unknown
        def reason_unknown(self): return reason
    class Backend:
        def execute(self, operation):
            pytest.fail("inconclusive verification must not execute")
    monkeypatch.setattr("ten_grd.engine.z3.Solver", InconclusiveSolver)
    result = Z3InvariantEngine().verify_selector(A, Eq("tenant_id", "tenant-a"))
    assert result.status is VerificationStatus.UNKNOWN
    assert reason in result.reason
    with tenant_scope(A), pytest.raises(VerificationError):
        TenantGuard(Backend()).get("item")


def test_solver_exception_never_executes(monkeypatch):
    def broken_solver(**kwargs):
        raise RuntimeError("sensitive internal detail")
    monkeypatch.setattr("ten_grd.engine.z3.Solver", broken_solver)
    result = Z3InvariantEngine().verify_selector(A, Eq("tenant_id", "tenant-a"))
    assert result.status is VerificationStatus.ERROR
    assert "sensitive" not in result.reason


def test_foreign_adapter_result_rejected_before_return():
    foreign = Record.create("tenant-b", "secret", {"payload": "private"})
    class LeakyAdapter:
        def execute(self, operation): return (foreign,)
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        TenantGuard(LeakyAdapter()).search()


def test_entire_batch_validated_before_return():
    own = Record.create("tenant-a", "a", "safe")
    foreign = Record.create("tenant-b", "b", "private")
    class MixedAdapter:
        def execute(self, operation): return (own, foreign)
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        TenantGuard(MixedAdapter()).search()


def test_matching_owner_wrong_record_rejected():
    class WrongTarget:
        def execute(self, operation):
            return (Record.create("tenant-a", "wrong", "data"),)
    with tenant_scope(A), pytest.raises(IsolationViolationError):
        TenantGuard(WrongTarget()).get("requested")


def test_lazy_adapter_result_rejected():
    class LazyAdapter:
        def execute(self, operation): return iter(())
    with tenant_scope(A), pytest.raises(InvalidOperationError):
        TenantGuard(LazyAdapter()).search()


def test_mismatched_evidence_blocks_execution():
    class WrongEvidence(Z3InvariantEngine):
        def verify_operation(self, context, operation):
            return replace(super().verify_operation(context, operation),
                           operation_digest="other-operation")
    class Backend:
        def execute(self, operation):
            pytest.fail("mismatched evidence must not execute")
    with tenant_scope(A), pytest.raises(VerificationError):
        TenantGuard(Backend(), engine=WrongEvidence()).get("item")


def test_wrong_policy_evidence_blocks_execution():
    class StaleEvidence(Z3InvariantEngine):
        def verify_operation(self, context, operation):
            return replace(super().verify_operation(context, operation),
                           policy_version="old-policy")
    with tenant_scope(A), pytest.raises(VerificationError):
        TenantGuard(InMemoryAdapter(), engine=StaleEvidence()).get("item")


def test_async_request_context_does_not_bleed(guard):
    async def run(context):
        with tenant_scope(context):
            await asyncio.sleep(0)
            guard.put("same", context.tenant_id)
            await asyncio.sleep(0)
            return guard.get("same").data
    async def main():
        return await asyncio.gather(run(A), run(B))
    assert asyncio.run(main()) == ["tenant-a", "tenant-b"]


def test_parallel_execution_has_separate_solver_contexts(guard):
    def run(index):
        tenant = f"tenant-{index}"
        with tenant_scope(TenantContext(tenant, "subject", f"request-{index}")):
            guard.put("same", tenant)
            return guard.get("same").data
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run, range(12))) == [f"tenant-{i}" for i in range(12)]


def test_model_matches_reference_evaluator_for_generated_selectors():
    # Eq-only logic has a finite representative domain: mentioned literals,
    # one other string, and missing. Exhaust all representatives per field.
    engine = Z3InvariantEngine()
    rng = random.Random(10)
    atoms = [Eq("tenant_id", "tenant-a"), Eq("tenant_id", "tenant-b"), Eq("topic", "x")]
    def generate(depth):
        if depth == 0:
            return rng.choice(atoms)
        choice = rng.randrange(4)
        if choice == 0:
            return rng.choice(atoms)
        if choice == 1:
            return Not(generate(depth - 1))
        cls = And if choice == 2 else Or
        return cls((generate(depth - 1), generate(depth - 1)))
    rows = []
    for tenant, topic in itertools.product([None, "tenant-a", "tenant-b", "other"],
                                           [None, "x", "other"]):
        rows.append({k: v for k, v in [("tenant_id", tenant), ("topic", topic)]
                     if v is not None})
    for _ in range(80):
        selector = generate(3)
        violation = any(matches(selector, row) and row.get("tenant_id") != "tenant-a"
                        for row in rows)
        result = engine.verify_selector(A, selector)
        expected = VerificationStatus.VIOLATION if violation else VerificationStatus.PROVEN
        assert result.status is expected, selector
