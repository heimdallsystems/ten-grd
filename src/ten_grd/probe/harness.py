"""Load and check the developer's harness module (tenguard_probe.py by default)."""
import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..context import validate_identifier
from ..exceptions import InvalidContextError


class HarnessError(Exception):
    """The harness is missing or does not follow the documented contract."""


@dataclass(frozen=True)
class Harness:
    path: Path
    tenants: tuple[str, ...]
    names: dict
    sign_in: Callable[[str], Any]
    seed: Callable[[Any, Any], None]
    ask: Callable[[Any, str], Any] | None
    tools: dict
    call_tool: Callable[[Any, str, dict], Any] | None
    jobs: dict = field(default_factory=dict)
    submit_job: Callable[[Any, str, dict], Any] | None = None
    run_jobs: Callable[..., None] | None = None
    job_result: Callable[[Any, Any], Any] | None = None
    job_retries: bool = False


def _accepts_fail_first(run_jobs):
    try:
        return "fail_first" in inspect.signature(run_jobs).parameters
    except (TypeError, ValueError):
        return False


def load_harness(path) -> Harness:
    path = Path(path).resolve()
    if not path.is_file():
        raise HarnessError(f"No harness at {path}. Create one with: ten-grd probe init")
    # The harness is the developer's own code, run with their permissions, like a test file.
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("tenguard_probe_harness", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:
        raise HarnessError(f"Could not import {path.name}: {type(error).__name__}: {error}") from None
    finally:
        sys.path.remove(str(path.parent))

    problems = []
    tenants = tuple(getattr(module, "TENANTS", ()) or ())
    if len(tenants) < 2:
        problems.append("TENANTS must list at least two synthetic test tenants")
    for tenant in tenants:
        try:
            validate_identifier(tenant, "tenant")
        except InvalidContextError:
            problems.append(f"invalid tenant identifier {tenant!r}")
    if len(set(tenants)) != len(tenants):
        problems.append("TENANTS contains duplicates")
    for name in ("sign_in", "seed"):
        if not callable(getattr(module, name, None)):
            problems.append(f"{name}() is required")
    ask = getattr(module, "ask", None)
    call_tool = getattr(module, "call_tool", None)
    tools = dict(getattr(module, "TOOLS", {}) or {})
    jobs = dict(getattr(module, "JOBS", {}) or {})
    if not callable(ask) and not (callable(call_tool) and tools) and not jobs:
        problems.append("provide ask(), or call_tool() with TOOLS, so the probe can reach the agent")
    for label, templates in (("TOOLS", tools), ("JOBS", jobs)):
        for name, template in templates.items():
            if type(template) is not dict or not any("{text}" in str(v) for v in template.values()):
                problems.append(f"{label}[{name!r}] must be a dict of arguments with a '{{text}}' placeholder")
    if jobs:
        for name in ("submit_job", "run_jobs", "job_result"):
            if not callable(getattr(module, name, None)):
                problems.append(f"{name}() is required when JOBS is set")
    if problems:
        raise HarnessError(f"{path.name} does not follow the harness contract:\n"
                           + "\n".join(f"  - {p}" for p in problems))
    names = dict(getattr(module, "TENANT_NAMES", {}) or {})
    return Harness(path, tenants, {t: names.get(t, t) for t in tenants}, module.sign_in, module.seed,
                   ask if callable(ask) else None, tools, call_tool if callable(call_tool) else None,
                   jobs, getattr(module, "submit_job", None), getattr(module, "run_jobs", None),
                   getattr(module, "job_result", None),
                   bool(jobs) and _accepts_fail_first(module.run_jobs))


TEMPLATE = '''"""TenGuard leak probe harness. Fill in the functions, then run: ten-grd probe run

Point this at a TEST environment with synthetic tenants and test credentials.
Never run the probe against production data.
"""

# At least two synthetic test tenants that exist in your test environment.
TENANTS = ["acme", "globex"]
# Optional display names; the probe uses them in cross-tenant prompts.
TENANT_NAMES = {"acme": "Acme Health", "globex": "Globex Finance"}


def sign_in(tenant):
    """Sign in a test user of `tenant` the way your app really does it; return a session.

    The probe never creates tenant context itself; it relies on your sign-in path.
    """
    raise NotImplementedError


def seed(session, canary):
    """Store canary.text for the signed-in tenant through your app's normal write path.

    canary has .tenant, .token, .title and .text. For a RAG agent, ingest it as a
    document; for a memory agent, save it as a memory.
    """
    raise NotImplementedError


def ask(session, message):
    """Send `message` to your agent as this session; return its reply.

    Return a string, or a dict such as {"answer": ..., "tool_calls": [...]}.
    Everything returned is searched for other tenants' canaries.
    """
    raise NotImplementedError


# Optional tool-level probing (no model needed). Map tool names to argument
# templates; "{text}" is replaced with attack text.
# TOOLS = {"search_knowledge": {"query": "{text}"}}
#
# def call_tool(session, name, arguments):
#     """Call your tool `name` with `arguments` as this session; return its raw result."""
#     raise NotImplementedError


# Optional background job probing. Without this, your workers are not exercised.
# Map job names to argument templates; "{text}" is replaced with attack text.
# JOBS = {"summarise": {"query": "{text}"}}
#
# def submit_job(session, name, arguments):
#     """Queue job `name` the way a request from this session would; return a handle."""
#     raise NotImplementedError
#
# def run_jobs(fail_first=False):
#     """Run everything queued, outside any request, on your real worker path.
#
#     With fail_first=True, make each job's first attempt fail so your retry path runs.
#     If you cannot inject that failure, leave the parameter out; the report will say
#     retries were not exercised.
#     """
#     raise NotImplementedError
#
# def job_result(session, handle):
#     """Read a job's result as this session; return everything the job produced."""
#     raise NotImplementedError
'''
