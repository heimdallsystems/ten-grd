"""Multi-step attacks on shared state: caches, background jobs and retries.

A single request cannot show these leaks: one tenant has to put data in motion before
another looks. Every output a tenant sees during a scenario is checked for canaries.
"""
import json
import secrets
from dataclasses import dataclass, field

from .attacks import canary_query, fill_template

ASK_IDENTICAL = "What is our internal refund policy? Include the policy reference code. (request {nonce})"
ASK_SIMILAR = "Tell me our internal refund policy and its reference code. (request {nonce})"
CACHE_FIX = "A shared cache returned one tenant's response to another. Include the tenant in the cache key and check ownership on every hit."
JOB_SCOPE_FIX = ("The worker does not run in the submitting tenant's scope. Carry the validated tenant "
                 "with the job and re-establish it for every attempt.")


@dataclass
class Outcome:
    attack: str
    kind: str          # "cache" or "job"
    title: str
    reader: str
    owner: str
    request: object
    fix_hint: str
    status: str = "completed"
    covered: bool = True            # False: a precondition failed, so silence proves nothing.
    order_dependent: bool | None = None   # Cache only: did the leak need the owner to go first?
    views: list = field(default_factory=list)  # (tenant, output) for everything a tenant saw.


def as_text(value):
    """Everything the agent returned, as one searchable string."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        return str(value)


def _attempt(call):
    """Run one step. A raised error is a rejection, which is a valid outcome for an attack."""
    try:
        return as_text(call()), None
    except Exception as error:
        return "", f"raised {type(error).__name__}"


def _cache_requests(harness, owner_canary):
    """(attack id, kind, title, request for the owner, request for the reader), each taking a nonce."""
    requests = []
    if harness.ask:
        requests.append(("cache-identical-prompt", "message", "Identical prompt after another tenant asked it",
                         lambda n: ASK_IDENTICAL.format(nonce=n), lambda n: ASK_IDENTICAL.format(nonce=n)))
        requests.append(("cache-similar-prompt", "message", "Reworded prompt after another tenant asked it",
                         lambda n: ASK_IDENTICAL.format(nonce=n), lambda n: ASK_SIMILAR.format(nonce=n)))
    if harness.call_tool:
        text = canary_query(owner_canary)
        for name, template in harness.tools.items():
            def call(nonce, name=name, template=template):
                return name, fill_template(template, f"{text} {nonce}")
            requests.append((f"cache-identical-tool:{name}", "tool", f"{name} repeated after another tenant called it",
                             call, call))
    return requests


def cache_scenarios(harness, send, canaries, reader, owner):
    """The owner primes a fresh request, then the reader repeats it.

    The reader first sends a fresh request. If that leaks too, the finding does not blame
    priming: the cause may be retrieval, or a similar-prompt cache matching an earlier entry.
    Primed responses work in both directions: the reverse tenant pair runs this with the
    roles swapped, which also covers poisoning.
    """
    token = canaries[owner].token
    for attack, kind, title, prime, read in _cache_requests(harness, canaries[owner]):
        def send_as(tenant, request):
            return _attempt(lambda: send(tenant, kind, request))

        baseline = read(secrets.token_hex(4))
        nonce = secrets.token_hex(4)
        result = Outcome(attack, "cache", title, reader, owner,
                         read(nonce) if kind == "message" else _tool_input(read(nonce)), CACHE_FIX)
        baseline_output, _ = send_as(reader, baseline)
        primed_output, primed_error = send_as(owner, prime(nonce))
        result.views += [(reader, baseline_output), (owner, primed_output)]
        if token in baseline_output:
            result.order_dependent = False
        if primed_error or token not in primed_output:
            result.status, result.covered = "not primed", False
            yield result
            continue
        output, error = send_as(reader, read(nonce))
        result.views.append((reader, output))
        result.status = error or "completed"
        if result.order_dependent is None:
            result.order_dependent = True
        yield result


def _tool_input(payload):
    return {"tool": payload[0], "arguments": payload[1]}


def job_controls(harness, sessions, canaries, tenant):
    """Each tenant must get its own canary back from each declared job."""
    canary = canaries[tenant]
    for name, template in harness.jobs.items():
        def run():
            handle = harness.submit_job(sessions[tenant], name, fill_template(template, canary_query(canary)))
            harness.run_jobs()
            return harness.job_result(sessions[tenant], handle)
        output, error = _attempt(run)
        yield name, (f"error: {error}" if error else output)


def job_scenarios(harness, sessions, canaries, reader, owner):
    """Attack the worker path of every declared job, then repeat through the retry path."""
    for name, template in harness.jobs.items():
        def submit(tenant, text, **extra):
            return harness.submit_job(sessions[tenant], name, {**fill_template(template, text), **extra})

        def owner_text():
            return canary_query(canaries[owner])

        def alone(attack, title, fix, retry=False, **extra):
            """The reader submits one job for the owner's text and reads the result."""
            arguments = {**fill_template(template, owner_text()), **extra}
            result = Outcome(f"{attack}:{name}", "job", title, reader, owner, {"job": name, "arguments": arguments}, fix)

            def run():
                handle = submit(reader, owner_text(), **extra)
                _run_jobs(harness, retry)
                return harness.job_result(sessions[reader], handle)
            output, error = _attempt(run)
            result.views.append((reader, output))
            result.status = error or "completed"
            return result

        def interleaved(attack, title, retry=False):
            """Both tenants have jobs queued together; each must get its own data and only that."""
            own = {t: canary_query(canaries[t]) for t in (owner, reader)}
            result = Outcome(f"{attack}:{name}", "job", title, reader, owner,
                             {"job": name, "submitted_by": [owner, reader], "arguments": "each tenant's own text"},
                             JOB_SCOPE_FIX)
            try:
                handles = {t: submit(t, own[t]) for t in (owner, reader)}
                _run_jobs(harness, retry)
            except Exception as error:
                result.status, result.covered = f"raised {type(error).__name__}", False
                return result
            for tenant, handle in handles.items():
                output, error = _attempt(lambda: harness.job_result(sessions[tenant], handle))
                result.views.append((tenant, output))
                if error or canaries[tenant].token not in output:
                    result.status, result.covered = "own result missing", False
            return result

        yield alone("job-other-text", "Job submitted with the other tenant's text", JOB_SCOPE_FIX)
        yield alone("job-tenant-argument", "Job submitted with an extra tenant_id",
                    "The job accepts a tenant argument; reject unexpected arguments and bind tenant from the session.",
                    tenant_id=owner)
        yield interleaved("job-interleaved", "Jobs from both tenants queued together")

        result = Outcome(f"job-foreign-result:{name}", "job", "Reads the other tenant's job result", reader, owner,
                         {"job": name, "result_of": owner},
                         "Job results are not bound to the submitting tenant; check ownership on every read.")
        try:
            handle = submit(owner, owner_text())
            harness.run_jobs()
            output, error = _attempt(lambda: harness.job_result(sessions[reader], handle))
            result.views.append((reader, output))
            result.status = error or "completed"
        except Exception as error:
            result.status, result.covered = f"raised {type(error).__name__}", False
        yield result

        if harness.job_retries:
            yield alone("job-retry-other-text", "Retried job with the other tenant's text", JOB_SCOPE_FIX, retry=True)
            yield interleaved("job-retry-interleaved", "Jobs from both tenants retried together", retry=True)


def _run_jobs(harness, retry):
    if retry:
        harness.run_jobs(fail_first=True)
    else:
        harness.run_jobs()
