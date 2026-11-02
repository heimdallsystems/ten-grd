"""The probe against real Postgres, Redis, Qdrant and RQ workers. Needs the integration services."""
import os
import sys
from pathlib import Path

import pytest

from ten_grd.probe import Harness, run_probe
from ten_grd.probe.report import markdown

HARNESSES = Path(__file__).parent / "probe_harnesses"
TOOLS = {"search": {"query": "{text}"}}
JOBS = {"summarise": {"query": "{text}"}}


@pytest.fixture
def make_agent(monkeypatch):
    settings = [os.getenv(name) for name in ("GRD_POSTGRES_DSN", "GRD_REDIS_URL", "GRD_QDRANT_URL")]
    if not all(settings):
        if os.getenv("GRD_REQUIRE_SERVICES"):
            pytest.fail("Required services are not configured")
        pytest.skip("Integration services not configured")
    if sys.platform == "win32":
        pytest.skip("RQ workers need a Unix platform")
    monkeypatch.syspath_prepend(str(HARNESSES))
    try:
        from live_stack import LiveAgent
    except ImportError as error:
        if os.getenv("GRD_REQUIRE_SERVICES"):
            pytest.fail(f"Live probe dependencies are missing: {error}")
        pytest.skip(f"Live probe dependencies are missing: {error}")
    agents = []

    def make(**options):
        agents.append(LiveAgent(*settings, **options))
        return agents[-1]
    yield make
    for agent in agents:
        agent.close()


def probe(agent, *, messages=True, tools=True, jobs=False, retries=True):
    def run_without_retries():
        agent.run_jobs()
    return run_probe(Harness(
        Path("live_stack.py"), tuple(agent.tenants), agent.names, agent.sign_in, agent.seed,
        agent.ask if messages else None, TOOLS if tools else {}, agent.call_tool if tools else None,
        JOBS if jobs else {}, agent.submit_job if jobs else None,
        (agent.run_jobs if retries else run_without_retries) if jobs else None,
        agent.job_result if jobs else None, jobs and retries))


def attacks(result):
    return {f["attack"] for f in result["findings"]}


def assert_real_evidence(result):
    for finding in result["findings"]:
        assert finding["as"] != finding["leaked_from"]
        assert "TGCANARY-" + finding["leaked_from"] in finding["evidence"]


def test_redis_cache_without_tenant_in_key_is_found(make_agent):
    result = probe(make_agent(cache="redis-unscoped"))
    assert result["verdict"] == "leak"
    assert_real_evidence(result)
    exact = [f for f in result["findings"] if f["attack"] == "cache-identical-prompt"]
    assert len({(f["as"], f["leaked_from"]) for f in exact}) == 2
    assert all(f["order_dependent"] is True for f in exact)
    assert any(f["attack"] == "cache-identical-tool:search" for f in result["findings"])
    # Retrieval is guarded and an exact cache cannot match a rewording.
    assert "cache-similar-prompt" not in attacks(result)


def test_qdrant_semantic_cache_without_tenant_filter_is_found(make_agent):
    result = probe(make_agent(cache="qdrant-unscoped"))
    assert result["verdict"] == "leak"
    assert_real_evidence(result)
    assert "cache-similar-prompt" in attacks(result)
    # A similar-prompt cache also serves the fresh request, so the report must not blame retrieval alone.
    if any(f.get("order_dependent") is False for f in result["findings"]):
        assert "any similar-prompt cache" in markdown(result)


@pytest.mark.parametrize("cache", ["redis-guarded", "qdrant-guarded"])
def test_ten_grd_caches_are_clean_with_scenarios_primed(make_agent, cache):
    result = probe(make_agent(cache=cache))
    assert result["verdict"] == "clean", result["findings"]
    assert all(c["own_canary_reached"] for c in result["controls"])
    scenarios = [a for a in result["attempts"] if a["kind"] == "cache"]
    assert scenarios and all(a["outcome"] == "completed" for a in scenarios)


@pytest.mark.parametrize("bug, attack", [
    ("lost-scope", "job-other-text:summarise"),
    ("stale-context", "job-interleaved:summarise"),
    ("retry-lost-scope", "job-retry-other-text:summarise"),
    ("unbound-results", "job-foreign-result:summarise"),
    ("tenant-argument", "job-tenant-argument:summarise"),
])
def test_each_rq_worker_bug_is_found_by_its_scenario(make_agent, bug, attack):
    result = probe(make_agent(bug=bug), jobs=True)
    assert result["verdict"] == "leak"
    assert attack in attacks(result)
    assert_real_evidence(result)


def test_rq_retry_bug_stays_hidden_without_a_retry(make_agent):
    result = probe(make_agent(bug="retry-lost-scope"), jobs=True, retries=False)
    assert result["verdict"] == "clean", result["findings"]
    assert next(c for c in result["coverage"] if c["class"] == "job retries")["ran"] is False


@pytest.mark.parametrize("worker", ["in-process", "separate-process"])
def test_correct_rq_worker_is_clean_through_real_retries(make_agent, worker):
    agent = make_agent(worker=worker)
    result = probe(agent, messages=False, tools=False, jobs=True)
    assert result["verdict"] == "clean", result["findings"]
    assert all(c["own_canary_reached"] for c in result["controls"])
    assert {"job-retry-other-text:summarise", "job-retry-interleaved:summarise"} <= {a["attack"] for a in result["attempts"]}
    # The retry path really ran: an interleaved retry returned each tenant's own data after a failure.
    assert all(a["outcome"] == "completed" for a in result["attempts"] if a["attack"].startswith("job-retry"))


@pytest.mark.parametrize("bug, attack", [
    ("lost-scope", "job-other-text:summarise"),
    ("retry-lost-scope", "job-retry-other-text:summarise"),
])
def test_separate_rq_worker_process_bugs_are_found(make_agent, bug, attack):
    result = probe(make_agent(bug=bug, worker="separate-process"), messages=False, tools=False, jobs=True)
    assert result["verdict"] == "leak"
    assert attack in attacks(result)
    assert_real_evidence(result)


def test_fully_guarded_stack_is_clean_with_every_class_run(make_agent):
    result = probe(make_agent(cache="qdrant-guarded"), jobs=True)
    assert result["verdict"] == "clean", result["findings"]
    assert all(c["ran"] for c in result["coverage"])
    assert all(c["own_canary_reached"] for c in result["controls"])
