"""Request-local scope supplied by the host authentication boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .exceptions import InvalidContextError

_MAX_IDENTIFIER = 256


def validate_identifier(value: str, name: str) -> None:
    if (not isinstance(value, str) or not value or len(value) > _MAX_IDENTIFIER
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise InvalidContextError(f"{name} must be a nonempty canonical identifier")


@dataclass(frozen=True)
class TenantContext:
    """Create only after the host validates identity and tenant membership."""
    tenant_id: str
    subject_id: str
    request_id: str
    policy_version: str = "1"

    def __post_init__(self):
        for name in ("tenant_id", "subject_id", "request_id", "policy_version"):
            validate_identifier(getattr(self, name), name)


_current: ContextVar[TenantContext | None] = ContextVar("ten_grd_context", default=None)


def current_context() -> TenantContext:
    context = _current.get()
    if context is None:
        raise InvalidContextError("No authenticated tenant context is active")
    return context


@contextmanager
def tenant_scope(context: TenantContext):
    """Bind validated context; always restore the prior scope on exit."""
    if type(context) is not TenantContext:
        raise InvalidContextError("Expected a validated TenantContext")
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
