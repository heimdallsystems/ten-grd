"""Run the probe: sign in, seed canaries, check controls, attack every tenant pair."""
import datetime
import json
import platform
from importlib import metadata

from .attacks import message_attacks, tool_attacks
from .canary import excerpt, make_canaries, tokens_in

LIMITS = ("A finding proves a cross-tenant leak for that input. No finding means none of the "
          "attempted attacks leaked; it is not proof of isolation. The probe exercises behaviour "
          "through your harness and does not verify source code. Use test environments and "
          "synthetic tenants only.")


def _text(value):
    """Everything the agent returned, as one searchable string."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        return str(value)


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
                text = canary.text.replace(canary.token, "").rstrip(" .")
                args = {k: (v.replace("{text}", text) if isinstance(v, str) else v) for k, v in template.items()}
                checks.append(("tool", (name, args)))
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

    # Attacks: every ordered pair of tenants.
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

    if result["findings"]:
        return _finish(result, "leak", f"{len(result['findings'])} cross-tenant leak(s) confirmed.")
    if not control_ok:
        return _finish(result, "inconclusive", "Some tenants could not reach their own canary, so a "
                                               "clean result would not mean anything. Fix seed() or ask().")
    return _finish(result, "clean", "No attempted attack exposed another tenant's canary.")


def _finding(as_tenant, victim, kind, attack_id, title, payload, output, token, fix_hint):
    return {"severity": "critical", "as": as_tenant, "leaked_from": victim, "kind": kind,
            "attack": attack_id, "title": title,
            "input": payload if kind == "message" else {"tool": payload[0], "arguments": payload[1]},
            "evidence": excerpt(output, token), "likely_fix": fix_hint}


def _finish(result, verdict, summary):
    result.update(verdict=verdict, summary=summary,
                  finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    return result
