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

The probe is included in Ten-Grd 0.3.0 and later. From your project:

```sh
python -m pip install "ten-grd>=0.3.0"
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
| 2 | inconclusive | Harness error, failed sign-in or seeding, or a tenant could not reach its own canary. |

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

Provide `ask`, or `call_tool` with `TOOLS`, or both. `TOOLS` maps each tool name to
an argument template, and `{text}` is replaced with attack text:

```python
TOOLS = {"search_knowledge": {"query": "{text}"}}
```

Everything your functions return is searched for other tenants' canaries, so return
tool results and traces as well as the final answer if you can.

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
4. **Reports.** Each finding gives the attack, the input, an evidence excerpt and a
   likely fix. The report also lists the control checks and every attempt.

## What a result means

A finding proves a cross-tenant leak for that input. No finding means none of the
attempted attacks leaked; it is not proof of isolation.

The probe exercises behaviour through your harness and does not verify source code.
It covers only the paths your harness reaches and only the attacks listed above. It
does not currently test cache poisoning, background jobs or retries, or use a model
as an attacker. Paths your harness does not call, such as a separate worker, are not
exercised.

Ten-Grd's guards and the probe are separate: running the probe does not add any
isolation to your agent. To enforce tenant scope, see [Examples](examples.md) and the
[threat model](threat-model.md).
