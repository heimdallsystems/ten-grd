"""Prove isolation of the supported selector model; fail closed otherwise."""
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import z3

from .context import TenantContext, validate_identifier
from .exceptions import (GuardError, IsolationViolationError,
                         InvalidOperationError, VerificationError)
from .operations import Operation, OperationKind
from .predicates import And, Eq, Not, Or, predicate_data, validate_predicate

MODEL_VERSION = "string-metadata-v1"


class VerificationStatus(str, Enum):
    PROVEN = "proven"
    VIOLATION = "violation"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    operation_digest: str
    policy_version: str
    reason: str
    model_version: str = MODEL_VERSION

    def require_proven(self):
        if self.status is VerificationStatus.VIOLATION:
            raise IsolationViolationError(self.reason)
        if self.status is not VerificationStatus.PROVEN:
            raise VerificationError(self.reason)


class Z3InvariantEngine:
    """No raw queries or caller-provided filter booleans are accepted."""

    def __init__(self, timeout_ms: int = 1000):
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 60_000:
            raise ValueError("timeout_ms must be between 1 and 60000")
        self.timeout_ms = timeout_ms

    def verify_selector(self, context: TenantContext, selector) -> VerificationResult:
        return self._verify(context, selector, "")

    def verify_operation(self, context: TenantContext,
                         operation: Operation) -> VerificationResult:
        try:
            if type(operation) is not Operation:
                raise InvalidOperationError("Expected an immutable Operation")
            operation.validate()
            if type(context) is not TenantContext or operation.tenant_id != context.tenant_id:
                return VerificationResult(VerificationStatus.VIOLATION, "",
                                          "", "Operation tenant differs from active context")
            if (operation.kind is OperationKind.PUT
                    and operation.record.tenant_id != context.tenant_id):
                raise IsolationViolationError("Write ownership differs from context")
            return self._verify(context, operation.selector, operation.digest)
        except GuardError:
            return VerificationResult(VerificationStatus.UNSUPPORTED, "", "",
                                      "Operation is outside the supported contract")

    def _verify(self, context, selector, digest):
        try:
            if type(context) is not TenantContext:
                raise InvalidOperationError("A validated context is required")
            validate_predicate(selector)
            if not digest:
                raw = json.dumps([context.tenant_id, context.policy_version,
                                  predicate_data(selector)], ensure_ascii=True)
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            # Per-call contexts prevent Z3 AST/solver sharing across requests.
            ctx = z3.Context()
            solver = z3.Solver(ctx=ctx)
            solver.set(timeout=self.timeout_ms)
            fields = {}

            def field(name):
                if name not in fields:
                    index = len(fields)
                    fields[name] = (z3.String(f"v{index}", ctx=ctx),
                                    z3.Bool(f"p{index}", ctx=ctx))
                return fields[name]

            def compile_node(node):
                if type(node) is Eq:
                    value, present = field(node.field)
                    return z3.And(present, value == z3.StringVal(node.value, ctx=ctx))
                if type(node) is And:
                    terms = [compile_node(p) for p in node.terms]
                    return z3.And(*terms) if terms else z3.BoolVal(True, ctx=ctx)
                if type(node) is Or:
                    terms = [compile_node(p) for p in node.terms]
                    return z3.Or(*terms) if terms else z3.BoolVal(False, ctx=ctx)
                return z3.Not(compile_node(node.term))

            selected = compile_node(selector)
            owner, owner_present = field("tenant_id")
            violation = z3.Or(z3.Not(owner_present),
                              owner != z3.StringVal(context.tenant_id, ctx=ctx))
            solver.add(selected, violation)
            outcome = solver.check()
            if outcome == z3.unsat:
                status, reason = VerificationStatus.PROVEN, "Selector preserves tenant ownership"
            elif outcome == z3.sat:
                status, reason = VerificationStatus.VIOLATION, "Selector can include foreign or unlabeled data"
            else:
                status = VerificationStatus.UNKNOWN
                reason = "Solver inconclusive: " + solver.reason_unknown()
            return VerificationResult(status, digest, context.policy_version, reason)
        except GuardError:
            return VerificationResult(VerificationStatus.UNSUPPORTED, digest, "",
                                      "Selector or context is unsupported")
        except Exception:
            # Do not expose solver/input details or interpret errors as success.
            return VerificationResult(VerificationStatus.ERROR, digest, "",
                                      "Verification failed")

    def verify_ownership(self, context: TenantContext, owner: str) -> None:
        if type(context) is not TenantContext:
            raise VerificationError("A validated context is required")
        try:
            validate_identifier(owner, "owner")
        except GuardError as exc:
            raise IsolationViolationError("Resource ownership is missing or invalid") from exc
        if owner != context.tenant_id:
            raise IsolationViolationError("Resource belongs to another tenant")
