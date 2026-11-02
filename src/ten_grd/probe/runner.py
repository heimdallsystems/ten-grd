"""Run the probe: sign in, seed canaries, check controls, attack every tenant pair."""
import datetime
import platform
from importlib import metadata
from itertools import chain

from .attacks import canary_query, fill_template, message_attacks, tool_attacks
from .canary import excerpt, make_canaries, tokens_in
from .scenarios import as_text as _text
from .scenarios import JOB_SCOPE_FIX, cache_scenarios, job_controls, job_scenarios

LIMITS = ("A finding proves a cross-tenant leak for that input. No finding means none of the "
          "attempted attacks leaked; it is not proof of isolation. Cache and job scenarios can "
          "also miss leaks that depend on expiry, similarity thresholds, timing or workers your "
          "harness does not run. The probe exercises behaviour through your harness and does not "
          "verify source code. Use test environments and synthetic tenants only.")


def _coverage(harness):
    """Which classes of attack this harness let the probe run, stated in every report."""
    return [
        {"class": "messages", "ran": bool(harness.ask), "detail": "ask()"},
        {"class": "tools", "ran": bool(harness.call_tool and harness.tools), "detail": "call_tool() with TOOLS"},
        {"class": "caches", "ran": bool(harness.ask or (harness.call_tool and harness.tools)),
         "detail": "repeated requests through ask() and call_tool()"},
        {"class": "background jobs", "ran": bool(harness.jobs), "detail": "JOBS, submit_job(), run_jobs(), job_result()"},
        {"class": "job retries", "ran": harness.job_retries, "detail": "run_jobs(fail_first=True)"},
    ]


def _version():
    try:
        return metadata.version("ten-grd")
    except metadata.PackageNotFoundError:
        return None


def run_probe(harness):
    started = datetime.datetime.now(datetime.timezone.utc)
    canaries = make_canaries(harness.tenants, harness.names)
    result = {"kind": "ten-grd-probe", "started_at": started.isoformat(timespec="seconds"),
              "harness": harness.path.name, "tenants": list(harness.tenants),
              "environment": {"ten_grd": _version(), "python": platform.python_version(),
                              "platform": platform.platform()},
              "coverage": _coverage(harness),
              "setup": [], "controls": [], "attempts": [], "findings": [], "limits": LIMITS}

    sessions = {}
    for tenant in harness.tenants:
        try:
            sessions[tenant] = harness.sign_in(tenant)
            harness.seed(sessions[tenant], canaries[tenant])
            result["setup"].append({"tenant": tenant, "status": "seeded"})
        except Exception as error:
            result["setup"].append({"tenant": tenant, "status": f"error: {type(error).__name__}: {error}"})
    if len(sessions) < len(harness.tenants):
        return _finish(result, "inconclusive", "Sign-in or seeding failed; see setup.")

    def attempt(tenant, kind, payload):
        session = sessions[tenant]
        if kind == "message":
            return _text(harness.ask(session, payload))
        name, arguments = payload
        return _text(harness.call_tool(session, name, arguments))

    # Controls: each tenant must reach its own canary, or "no leak" would mean nothing.
    control_ok = True
    for tenant in harness.tenants:
        canary = canaries[tenant]
        checks = []
        if harness.ask:
            checks.append(("message", "What is our internal refund policy? Include the policy reference code."))
        for name, template in harness.tools.items():
            if harness.call_tool:
                checks.append(("tool", (name, fill_template(template, canary_query(canary)))))
        for kind, payload in checks:
            try:
                output = attempt(tenant, kind, payload)
                reached = canary.token in output
                foreign = [t for t in tokens_in(output, canaries) if t != tenant]
            except Exception as error:
                reached, foreign, output = False, [], f"error: {type(error).__name__}: {error}"
            result["controls"].append({"tenant": tenant, "kind": kind,
                                       "via": payload if kind == "message" else payload[0],
                                       "own_canary_reached": reached})
            control_ok &= reached
            for other in foreign:  # A leak can already show up in a control check.
                result["findings"].append(_finding(tenant, other, kind, "control", "Own-data request",
                                                   payload, output, canaries[other].token,
                                                   "Results are not restricted to the signed-in tenant."))
        for name, output in job_controls(harness, sessions, canaries, tenant):
            reached = canary.token in output
            result["controls"].append({"tenant": tenant, "kind": "job", "via": name, "own_canary_reached": reached})
            control_ok &= reached
            for other in (t for t in tokens_in(output, canaries) if t != tenant):
                result["findings"].append(_finding(tenant, other, "job", "control", "Own job result",
                                                   {"job": name}, output, canaries[other].token, JOB_SCOPE_FIX))

    # Attacks: every ordered pair of tenants.
    uncovered = 0
    for tenant in harness.tenants:
        for other in harness.tenants:
            if other == tenant:
                continue
            attacks = (message_attacks(harness.names[other], canaries[other]) if harness.ask else [])
            if harness.call_tool:
                attacks += tool_attacks(harness.tools, canaries[other])
            for attack in attacks:
                try:
                    output = attempt(tenant, attack.kind, attack.payload)
                    outcome = "completed"
                except Exception as error:
                    output, outcome = "", f"raised {type(error).__name__}"
                leaked = [t for t in tokens_in(output, canaries) if t != tenant]
                result["attempts"].append({"as": tenant, "target": other, "attack": attack.id,
                                           "kind": attack.kind, "outcome": outcome,
                                           "leaked_from": leaked})
                for victim in leaked:
                    result["findings"].append(_finding(tenant, victim, attack.kind, attack.id, attack.title,
                                                       attack.payload, output, canaries[victim].token,
                                                       attack.fix_hint))
            for outcome in chain(cache_scenarios(harness, attempt, canaries, tenant, other),
                                 job_scenarios(harness, sessions, canaries, tenant, other)):
                uncovered += not outcome.covered
                _record_scenario(result, outcome, canaries)

    if result["findings"]:
        return _finish(result, "leak", f"{len(result['findings'])} cross-tenant leak(s) confirmed.")
    if not control_ok:
        return _finish(result, "inconclusive", "Some tenants could not reach their own canary, so a "
                                               "clean result would not mean anything. Fix seed() or ask().")
    if uncovered:
        return _finish(result, "inconclusive", f"{uncovered} cache or job scenario(s) could not set up their "
                                               "own data, so a clean result would not mean anything. See attempts.")
    return _finish(result, "clean", "No attempted attack exposed another tenant's canary.")


def _record_scenario(result, outcome, canaries):
    leaked = []
    for viewer, output in outcome.views:
        for victim in tokens_in(output, canaries):
            if victim == viewer or (viewer, victim) in leaked:
                continue
            leaked.append((viewer, victim))
            result["findings"].append(_finding(viewer, victim, outcome.kind, outcome.attack, outcome.title,
                                               outcome.request, output, canaries[victim].token, outcome.fix_hint,
                                               outcome.order_dependent))
    attempt = {"as": outcome.reader, "target": outcome.owner, "attack": outcome.attack, "kind": outcome.kind,
               "outcome": outcome.status, "leaked_from": [victim for _, victim in leaked]}
    if outcome.order_dependent is not None:
        attempt["order_dependent"] = outcome.order_dependent
    result["attempts"].append(attempt)


def _finding(as_tenant, victim, kind, attack_id, title, payload, output, token, fix_hint, order_dependent=None):
    finding = {"severity": "critical", "as": as_tenant, "leaked_from": victim, "kind": kind,
               "attack": attack_id, "title": title,
               "input": {"tool": payload[0], "arguments": payload[1]} if kind == "tool" else payload,
               "evidence": excerpt(output, token), "likely_fix": fix_hint}
    if order_dependent is not None:
        finding["order_dependent"] = order_dependent
    return finding


def _finish(result, verdict, summary):
    result.update(verdict=verdict, summary=summary,
                  finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    return result
