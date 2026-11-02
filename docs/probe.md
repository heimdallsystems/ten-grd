# Leak probe (preview)

`ten-grd probe` checks an agent you already built for cross-tenant data leaks,
whether or not it uses Ten-Grd. It plants a unique canary record in each synthetic
test tenant, signs in as one tenant, tries to reach the others' canaries, and
reports every canary that crosses over.

The verdict comes only from the canaries; no model judges whether data leaked.

!!! warning "Test environments only"
    Run the probe against a test environment with synthetic tenants and test
    credentials, never production data. The probe writes records through your
    application's write path and sends it hostile requests.

## Run it

The probe is included in Ten-Grd 0.3.0 and later; the cache, background job and
retry scenarios need 0.4.0. From your project:

```sh
python -m pip install "ten-grd>=0.4.0"
ten-grd probe init
ten-grd probe run --out leak-report.md --json leak-report.json
```

`init` writes `tenguard_probe.py`, a small harness for you to fill in. `run` writes
a Markdown report, and a JSON record if you ask for one. Use `--harness` to load a
harness from another path. `python -m ten_grd.probe` runs the same command.

| Exit code | Verdict | Meaning |
| --- | --- | --- |
| 0 | clean | None of the attempted attacks exposed another tenant's canary. |
| 1 | leak | At least one cross-tenant leak was confirmed. |
| 2 | inconclusive | Harness error, failed sign-in or seeding, a tenant could not reach its own canary, or a cache or job scenario could not set up its own data. |

Because the exit code is non-zero for a leak or an inconclusive run, the command
can gate CI.

## Write the harness

Only your code knows how a user becomes a tenant, so the probe never creates tenant
context itself. The harness connects it to your application:

| Name | Required | Purpose |
| --- | --- | --- |
| `TENANTS` | Yes | At least two synthetic test tenants that exist in your test environment. |
| `TENANT_NAMES` | No | Display names, used in cross-tenant prompts. |
| `sign_in(tenant)` | Yes | Sign in a test user the way your application really does; return a session. |
| `seed(session, canary)` | Yes | Store `canary.text` through your normal write path. |
| `ask(session, message)` | See below | Send a message to your agent; return its reply as a string or dict. |
| `TOOLS`, `call_tool(session, name, arguments)` | See below | Call tools directly with hostile arguments. No model needed. |
| `JOBS`, `submit_job(session, name, arguments)`, `run_jobs(fail_first=False)`, `job_result(session, handle)` | No | Exercise your background workers. |

Provide `ask`, or `call_tool` with `TOOLS`, or both. `TOOLS` maps each tool name to
an argument template, and `{text}` is replaced with attack text:

```python
TOOLS = {"search_knowledge": {"query": "{text}"}}
```

Everything your functions return is searched for other tenants' canaries, so return
tool results and traces as well as the final answer if you can.

Cache scenarios need nothing extra: they run through `ask` and `call_tool`, so they
reach a cache only if those functions go through it. Call your real request path,
not a shortcut around the cache.

### Background jobs and retries

The probe cannot reach a worker by itself, so job scenarios run only if you declare
them. `JOBS` works like `TOOLS`:

```python
JOBS = {"summarise": {"query": "{text}"}}


def submit_job(session, name, arguments):
    """Queue the job the way a request from this session would; return a handle."""
    return app.jobs.enqueue(session, name, arguments)


def run_jobs(fail_first=False):
    """Run everything queued, outside any request, on your real worker path."""
    app.worker.drain(fail_first_attempt=fail_first)


def job_result(session, handle):
    """Read the result as this session."""
    return app.jobs.result(session, handle)
```

With `fail_first=True`, make each job's first attempt fail so your retry path runs.
If you cannot inject that failure, leave the parameter out and the report will say
retries were not exercised. `app.jobs` and `app.worker` stand in for your own code.

## Try it on a deliberately leaky agent

This harness is a complete toy agent. Its `ask` returns every tenant's records:

```python
TENANTS = ["acme", "globex"]
TENANT_NAMES = {"acme": "Acme Health", "globex": "Globex Finance"}
RECORDS = []


def sign_in(tenant):
    return {"tenant": tenant}


def seed(session, canary):
    RECORDS.append({"tenant": session["tenant"], "text": canary.text})


def ask(session, message):
    return "\n".join(record["text"] for record in RECORDS)
```

Save it as `tenguard_probe.py` and run `ten-grd probe run`. The verdict is `leak`,
exit code 1, with findings in both directions. Now restrict the read to the
signed-in tenant:

```python
def ask(session, message):
    return "\n".join(record["text"] for record in RECORDS
                     if record["tenant"] == session["tenant"])
```

The verdict becomes `clean`, exit code 0, with every control check passing.

## What it does

1. **Seeds canaries.** Each tenant gets a record with a unique marker, written
   through your `seed` function while signed in as that tenant.
2. **Runs control checks.** Each tenant must be able to reach its own canary. If one
   cannot, a clean result would mean nothing, so the run is inconclusive.
3. **Attacks every ordered pair of tenants.** Through `ask`: a direct request,
   instruction injection, the other tenant's exact text, and a request to switch
   `tenant_id`. Through `call_tool`: the other tenant's text, an extra `tenant_id`
   argument, and a filter-widening string.
4. **Tries shared caches.** One tenant sends a fresh request that returns its own
   canary, then the other sends the identical request, through `ask` and through
   each tool, or a reworded one through `ask`. Because every pair runs in both
   directions, this also covers one tenant poisoning the cache for another. The
   second tenant first sends a fresh request, so a finding can say whether the leak
   needed the other tenant to go first, which points at a shared cache.
5. **Tries background jobs, if declared.** A job with the other tenant's text, a job
   with an extra `tenant_id`, jobs from both tenants queued together, and reading
   the other tenant's job result. If `run_jobs` accepts `fail_first`, the text and
   queued-together scenarios repeat through your retry path.
6. **Reports.** Each finding gives the attack, the input, an evidence excerpt and a
   likely fix. The report also lists which attack classes ran, the control checks
   and every attempt.

## What a result means

A finding proves a cross-tenant leak for that input. No finding means none of the
attempted attacks leaked; it is not proof of isolation.

The probe exercises behaviour through your harness and does not verify source code.
It covers only the paths your harness reaches and only the attacks listed above, and
it does not use a model as an attacker. Paths your harness does not call, such as a
worker you did not declare in `JOBS`, are not exercised; the report's coverage table
shows which classes ran.

Cache and job scenarios can also miss real leaks. A cache may not store a response,
may expire it, or may use a similarity threshold the reworded prompt does not meet.
A race may need timing that `run_jobs` does not reproduce. The probe cannot tell
whether a request was served from a cache, so "needed the other tenant to go first"
points at shared state and does not name the component. If a fresh request leaks too,
the cause may be retrieval, or a similar-prompt cache matching an entry from earlier
in the run, so the report does not rule out either.

Ten-Grd's guards and the probe are separate: running the probe does not add any
isolation to your agent. To enforce tenant scope, see [Examples](examples.md) and the
[threat model](threat-model.md).
