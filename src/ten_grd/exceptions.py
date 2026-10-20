"""Failures never authorize an operation."""


class GuardError(Exception):
    """Base error raised before protected output is exposed."""


class InvalidContextError(GuardError):
    """A valid host-authenticated tenant context is required."""


class InvalidOperationError(GuardError):
    """The operation is outside the supported contract."""


class VerificationError(GuardError):
    """Verification did not establish isolation."""


class IsolationViolationError(VerificationError):
    """A modeled violation or foreign-owned result was detected."""
