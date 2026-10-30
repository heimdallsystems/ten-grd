"""The leak probe finds real leaks in a leaky agent and none in the same agent on ten-grd."""
import json
from pathlib import Path

import pytest

from ten_grd.probe import HarnessError, load_harness, run_probe
from ten_grd.probe.cli import main

HARNESSES = Path(__file__).parent / "probe_harnesses"


def probe(name):
    return run_probe(load_harness(HARNESSES / f"{name}_harness.py"))


def test_leaky_agent_leaks_are_found_with_evidence():
    result = probe("leaky")
    assert result["verdict"] == "leak"
    attacks = {f["attack"] for f in result["findings"]}
    # Unfiltered search, the prompt-driven tenant switch and the tenant_id tool argument.
    assert {"direct-request", "tenant-switch", "tool-tenant-argument:search"} <= attacks
    for finding in result["findings"]:
        assert finding["as"] != finding["leaked_from"]
        assert "TGCANARY-" + finding["leaked_from"] in finding["evidence"]
    pairs = {(f["as"], f["leaked_from"]) for f in result["findings"]}
    assert pairs == {("acme", "globex"), ("globex", "acme")}


def test_guarded_agent_is_clean_and_controls_pass():
    result = probe("guarded")
    assert result["verdict"] == "clean", result["findings"]
    assert result["controls"] and all(c["own_canary_reached"] for c in result["controls"])
    rejected = [a for a in result["attempts"] if a["attack"].startswith("tool-tenant-argument")]
    assert rejected and all(a["outcome"].startswith("raised") for a in rejected)


def test_clean_without_working_controls_is_inconclusive(tmp_path):
    harness = tmp_path / "blind_harness.py"
    harness.write_text(
        "TENANTS = ['acme', 'globex']\n"
        "def sign_in(t): return t\n"
        "def seed(s, c): pass\n"           # Canaries never stored...
        "def ask(s, m): return 'I cannot help with that.'\n",  # ...so silence proves nothing.
        encoding="utf-8")
    result = run_probe(load_harness(harness))
    assert result["verdict"] == "inconclusive" and not result["findings"]


def test_canaries_are_unique_per_tenant_and_run():
    from ten_grd.probe.canary import make_canaries
    names = {"acme": "Acme", "globex": "Globex"}
    first, second = make_canaries(names, names), make_canaries(names, names)
    tokens = [c.token for c in (*first.values(), *second.values())]
    assert len(set(tokens)) == 4
    assert all(first[t].token.startswith(f"TGCANARY-{t}-") and first[t].token in first[t].text for t in names)


@pytest.mark.parametrize("source, message", [
    ("TENANTS = ['acme']\ndef sign_in(t): pass\ndef seed(s, c): pass\ndef ask(s, m): pass\n", "at least two"),
    ("TENANTS = ['acme', 'acme']\ndef sign_in(t): pass\ndef seed(s, c): pass\ndef ask(s, m): pass\n", "duplicates"),
    ("TENANTS = ['a', 'b']\ndef seed(s, c): pass\ndef ask(s, m): pass\n", "sign_in() is required"),
    ("TENANTS = ['a', 'b']\ndef sign_in(t): pass\ndef seed(s, c): pass\n", "provide ask()"),
    ("TENANTS = ['a', 'b']\nTOOLS = {'x': {'q': 'no placeholder'}}\ndef sign_in(t): pass\n"
     "def seed(s, c): pass\ndef call_tool(s, n, a): pass\n", "placeholder"),
])
def test_harness_contract_is_checked(tmp_path, source, message):
    path = tmp_path / "bad_harness.py"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(HarnessError, match=message.replace("(", r"\(").replace(")", r"\)")):
        load_harness(path)


def test_cli_init_and_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["probe", "init"]) == 0 and Path("tenguard_probe.py").exists()
    assert main(["probe", "init"]) == 2  # Refuses to overwrite.
    assert main(["probe", "run"]) == 2   # Template not filled in: setup fails, inconclusive.

    leaky = main(["probe", "run", "--harness", str(HARNESSES / "leaky_harness.py"),
                  "--out", "leaky.md", "--json", "leaky.json"])
    assert leaky == 1
    report = Path("leaky.md").read_text(encoding="utf-8")
    assert report.startswith("# TenGuard leak probe: LEAK FOUND") and "not proof of isolation" in report
    assert json.loads(Path("leaky.json").read_text(encoding="utf-8"))["verdict"] == "leak"

    clean = main(["probe", "run", "--harness", str(HARNESSES / "guarded_harness.py"), "--out", "clean.md"])
    assert clean == 0
    assert "No leak found" in Path("clean.md").read_text(encoding="utf-8")
