"""Cache and job scenarios each find the one bug they exist for, and pass once it is fixed."""
from pathlib import Path

import pytest

from ten_grd.probe import HarnessError, load_harness, run_probe
from ten_grd.probe.report import markdown

HARNESSES = Path(__file__).parent / "probe_harnesses"
BASE = f"""
import sys
sys.path.insert(0, {str(HARNESSES)!r})
from toy_agents import CachedAgent, GuardedAgent, JobAgent, SimilarCachedAgent

AGENT = {{agent}}
TENANTS = ["acme", "globex"]
TENANT_NAMES = {{{{"acme": "Acme Health", "globex": "Globex Finance"}}}}


def sign_in(tenant):
    return AGENT.sign_in(tenant)


def seed(session, canary):
    AGENT.ingest(session, canary.text)

"""
MESSAGES = """
def ask(session, message):
    return AGENT.ask(session, message)

"""
TOOLS = """
TOOLS = {"search": {"query": "{text}"}}


def call_tool(session, name, arguments):
    return AGENT.search(session, **arguments)

"""
JOBS = """
JOBS = {"summarise": {"query": "{text}"}}


def submit_job(session, name, arguments):
    return AGENT.submit(session, name, arguments)


def job_result(session, handle):
    return AGENT.result(session, handle)

"""
RUN_WITH_RETRIES = """
def run_jobs(fail_first=False):
    AGENT.run(fail_first)
"""
RUN_WITHOUT_RETRIES = """
def run_jobs():
    AGENT.run()
"""


def probe(tmp_path, agent, *parts):
    path = tmp_path / "harness.py"
    path.write_text(BASE.format(agent=agent) + "".join(parts), encoding="utf-8")
    return run_probe(load_harness(path))


def attacks(result):
    return {f["attack"] for f in result["findings"]}


def job_probe(tmp_path, bug):
    return probe(tmp_path, f"JobAgent({bug!r})", JOBS, RUN_WITH_RETRIES)


def test_cache_keyed_without_tenant_is_found_and_pinned_on_the_cache(tmp_path):
    result = probe(tmp_path, "CachedAgent()", MESSAGES, TOOLS)
    assert result["verdict"] == "leak"
    found = [f for f in result["findings"] if f["attack"] == "cache-identical-prompt"]
    assert {(f["as"], f["leaked_from"]) for f in found} == {("acme", "globex"), ("globex", "acme")}
    assert all(f["order_dependent"] is True for f in found)
    assert "cache-identical-tool:search" in attacks(result)
    # An exact cache must not be reported as a similar-prompt hit.
    assert not [f for f in result["findings"] if f["attack"] == "cache-similar-prompt" and f.get("order_dependent")]
    assert "this points at a shared cache" in markdown(result)


def test_similar_prompt_cache_is_found(tmp_path):
    assert "cache-similar-prompt" in attacks(probe(tmp_path, "SimilarCachedAgent()", MESSAGES))


@pytest.mark.parametrize("agent", ["CachedAgent(tenant_in_key=True)", "SimilarCachedAgent(tenant_in_key=True)"])
def test_cache_with_tenant_in_key_is_clean(tmp_path, agent):
    result = probe(tmp_path, agent, MESSAGES, TOOLS)
    assert result["verdict"] == "clean", result["findings"]
    assert any(a["attack"] == "cache-identical-prompt" for a in result["attempts"])


def test_leak_without_a_cache_is_not_blamed_on_the_cache():
    result = run_probe(load_harness(HARNESSES / "leaky_harness.py"))
    found = [f for f in result["findings"] if f["attack"] == "cache-identical-prompt"]
    assert any(f.get("order_dependent") is False for f in found)
    assert not any(f.get("order_dependent") for f in found)


def test_scenario_that_cannot_prime_is_inconclusive(tmp_path):
    # Controls pass, but the agent answers only the exact control question, so priming proves nothing.
    path = tmp_path / "harness.py"
    path.write_text(
        "TENANTS = ['acme', 'globex']\nDOCS = {}\n"
        "def sign_in(t): return t\n"
        "def seed(s, c): DOCS[s] = c.text\n"
        "def ask(s, m): return DOCS[s] if '(request' not in m else 'Sorry.'\n", encoding="utf-8")
    result = run_probe(load_harness(path))
    assert result["verdict"] == "inconclusive" and not result["findings"]
    assert all(c["own_canary_reached"] for c in result["controls"])
    assert any(a["outcome"] == "not primed" for a in result["attempts"])


@pytest.mark.parametrize("bug, attack", [
    ("lost-scope", "job-other-text:summarise"),
    ("stale-context", "job-interleaved:summarise"),
    ("retry-lost-scope", "job-retry-other-text:summarise"),
    ("unbound-results", "job-foreign-result:summarise"),
    ("tenant-argument", "job-tenant-argument:summarise"),
])
def test_each_job_bug_is_found_by_its_scenario(tmp_path, bug, attack):
    result = job_probe(tmp_path, bug)
    assert result["verdict"] == "leak"
    assert attack in attacks(result)
    for finding in result["findings"]:
        assert finding["as"] != finding["leaked_from"]
        assert "TGCANARY-" + finding["leaked_from"] in finding["evidence"]


def test_retry_bug_is_invisible_until_retries_run(tmp_path):
    result = probe(tmp_path, "JobAgent('retry-lost-scope')", JOBS, RUN_WITHOUT_RETRIES)
    assert result["verdict"] == "clean", result["findings"]
    retries = next(c for c in result["coverage"] if c["class"] == "job retries")
    assert retries["ran"] is False
    assert "| job retries | NO |" in markdown(result)


def test_correct_worker_is_clean_with_job_controls_passing(tmp_path):
    result = job_probe(tmp_path, None)
    assert result["verdict"] == "clean", result["findings"]
    job_controls = [c for c in result["controls"] if c["kind"] == "job"]
    assert len(job_controls) == 2 and all(c["own_canary_reached"] for c in job_controls)
    ran = {a["attack"] for a in result["attempts"]}
    assert {"job-other-text:summarise", "job-tenant-argument:summarise", "job-interleaved:summarise",
            "job-foreign-result:summarise", "job-retry-other-text:summarise",
            "job-retry-interleaved:summarise"} <= ran
    assert all(c["ran"] for c in result["coverage"] if c["class"] in ("background jobs", "job retries"))


def test_worker_that_never_finishes_is_inconclusive(tmp_path):
    result = probe(tmp_path, "JobAgent()", JOBS, "\ndef run_jobs(fail_first=False):\n    pass\n")
    assert result["verdict"] == "inconclusive" and not result["findings"]


def test_jobs_require_the_full_contract(tmp_path):
    path = tmp_path / "harness.py"
    path.write_text("TENANTS = ['a', 'b']\nJOBS = {'x': {'q': '{text}'}}\ndef sign_in(t): pass\n"
                    "def seed(s, c): pass\ndef submit_job(s, n, a): pass\n", encoding="utf-8")
    with pytest.raises(HarnessError) as error:
        load_harness(path)
    assert "run_jobs() is required" in str(error.value) and "job_result() is required" in str(error.value)


def test_harness_without_jobs_reports_them_as_not_run():
    result = run_probe(load_harness(HARNESSES / "guarded_harness.py"))
    assert result["verdict"] == "clean", result["findings"]
    assert next(c for c in result["coverage"] if c["class"] == "background jobs")["ran"] is False
    assert "| background jobs | NO |" in markdown(result)
