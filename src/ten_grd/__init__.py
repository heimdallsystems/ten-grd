"""Tennant Guard: tenant isolation primitives for agentic developers."""
from .adapters import InMemoryAdapter, IsolationAdapter
from .client import TenantGuard
from .context import TenantContext, current_context, tenant_scope
from .engine import VerificationResult, VerificationStatus, Z3InvariantEngine
from .exceptions import (GuardError, InvalidContextError, InvalidOperationError,
                         IsolationViolationError, VerificationError, AdapterError)
from .predicates import And, Eq, Not, Or

__version__ = "0.4.0"
__all__ = [
    "TenantGuard", "TenantContext", "tenant_scope", "current_context",
    "InMemoryAdapter", "IsolationAdapter", "Z3InvariantEngine",
    "VerificationResult", "VerificationStatus", "Eq", "And", "Or", "Not",
    "GuardError", "InvalidContextError", "InvalidOperationError",
    "IsolationViolationError", "VerificationError", "AdapterError",
]
