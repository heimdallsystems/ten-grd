"""Markdown report for people; the JSON record is the run result itself."""
import json

VERDICT = {"leak": "LEAK FOUND", "clean": "No leak found", "inconclusive": "INCONCLUSIVE"}


def markdown(result):
    lines = [f"# TenGuard leak probe: {VERDICT[result['verdict']]}", "",
             result["summary"], "",
             f"- Harness: `{result['harness']}`",
             f"- Tenants: {', '.join(result['tenants'])}",
             f"- Attempts: {len(result['attempts'])} across every ordered tenant pair",
             f"- Run: {result['started_at']} to {result['finished_at']} "
             f"(ten-grd {result['environment']['ten_grd']}, Python {result['environment']['python']})", ""]

    if result["findings"]:
        lines += ["## Findings", ""]
        for number, f in enumerate(result["findings"], 1):
            shown = f["input"] if isinstance(f["input"], str) else json.dumps(f["input"], ensure_ascii=False)
            lines += [f"### {number}. [{f['severity'].upper()}] {f['leaked_from']} data reached {f['as']}: {f['title']}",
                      "", f"- Attack: `{f['attack']}` ({f['kind']})",
                      f"- Signed in as: `{f['as']}`",
                      f"- Input: `{shown}`",
                      f"- Evidence: `{f['evidence']}`"]
            if "order_dependent" in f:
                lines.append("- Needed another tenant to go first: yes, this points at a shared cache"
                             if f["order_dependent"] else
                             "- Needed another tenant to go first: not shown, a fresh request leaked too. "
                             "Check retrieval, and any similar-prompt cache that could match an earlier entry")
            lines += [f"- Likely fix: {f['likely_fix']}", ""]

    lines += ["## Coverage", "", "Attack classes this harness let the probe run. Nothing is known about the others.", "",
              "| Class | Ran | Needs |", "| --- | --- | --- |"]
    lines += [f"| {c['class']} | {'yes' if c['ran'] else 'NO'} | {c['detail']} |" for c in result["coverage"]]
    lines += ["", "## Controls", "", "Each tenant must reach its own canary for a clean result to mean anything.", "",
              "| Tenant | Via | Own canary reached |", "| --- | --- | --- |"]
    lines += [f"| {c['tenant']} | {c['kind']}: {c['via'] if c['kind'] != 'message' else 'own-policy question'} "
              f"| {'yes' if c['own_canary_reached'] else 'NO'} |" for c in result["controls"]]
    if any(not s["status"] == "seeded" for s in result["setup"]):
        lines += ["", "## Setup problems", ""] + [f"- {s['tenant']}: {s['status']}" for s in result["setup"]]

    lines += ["", "## Attempts", "", "| As | Target | Attack | Outcome | Leaked from |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {a['as']} | {a['target']} | `{a['attack']}` | {a['outcome']} | {', '.join(a['leaked_from']) or '-'} |"
              for a in result["attempts"]]
    lines += ["", "## What this result means", "", result["limits"], ""]
    return "\n".join(lines)
