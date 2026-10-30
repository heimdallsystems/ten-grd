"""Tenant leak probe: plant canaries per tenant, attack across tenants, report leaks.

A finding proves a leak for that input. No finding means the attempted attacks did
not leak; it is not proof of isolation. Run against test environments only.
"""
from .harness import Harness, HarnessError, load_harness
from .runner import run_probe

__all__ = ["Harness", "HarnessError", "load_harness", "run_probe"]
