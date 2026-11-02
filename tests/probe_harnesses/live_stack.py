"""A probe target on real services: documents in Postgres, caches in Redis and Qdrant, RQ workers.

`cache` and `bug` each select one isolation mistake. With the guarded settings, every read
goes through ten-grd. No model is involved; answers are the best keyword match.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import redis
from qdrant_client import QdrantClient, models
from rq import Queue, Retry, SimpleWorker, get_current_job
from rq.job import Job

from ten_grd import Eq, TenantContext, TenantGuard, tenant_scope
from ten_grd.integrations.cache import RedisCache
from ten_grd.integrations.postgres import PostgresAdapter
from ten_grd.integrations.qdrant import QdrantRetriever
from ten_grd.integrations.semantic_cache import SemanticCache

WORD = re.compile(r"[a-z0-9]+")
DIMENSION = 128
SIMILAR = 0.6
_worker_context = None  # Stale-context bug: set by the first job, never reset.


def _overlap(query, text):
    return len(set(WORD.findall(query.lower())) & set(WORD.findall(text.lower())))


def _best(query, texts):
    return sorted(texts, key=lambda t: -_overlap(query, t))[:1]


def _embed(text):
    """A deterministic bag-of-words vector: reworded prompts really are close under cosine."""
    vector = [0.0] * DIMENSION
    for word in set(WORD.findall(text.lower())):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSION] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _context(tenant):
    return TenantContext(tenant, f"user@{tenant}", f"probe-{tenant}")


def _guarded_search(dsn, context, query):
    guard = TenantGuard(PostgresAdapter(dsn))
    with tenant_scope(context):
        records = guard.search(Eq("kind", "doc"))
    return _best(query, [r.data["text"] for r in records])


def _raw_search(dsn, tenants, query):
    with psycopg.connect(dsn) as connection:
        rows = connection.execute("SELECT data_json FROM grd_records WHERE tenant_id = ANY(%s)", (tenants,)).fetchall()
    return _best(query, [json.loads(row[0])["text"] for row in rows])


def summarise(payload):
    """The RQ job. It runs in the worker, outside any request."""
    global _worker_context
    job = get_current_job()
    retrying = job.retries_left is not None and job.retries_left < 1
    if not retrying and job.connection.get(payload["fail_flag"]) == b"1":
        raise RuntimeError("Injected first-attempt failure")
    bug, dsn, query = payload["bug"], payload["dsn"], payload["query"]
    if bug == "lost-scope" or (bug == "retry-lost-scope" and retrying):
        return _raw_search(dsn, payload["tenants"], query)
    if payload["tenant_argument"]:
        return _raw_search(dsn, [payload["tenant_argument"]], query)
    context = _context(payload["tenant"])  # Re-established from the job, per the threat model.
    if bug == "stale-context":
        _worker_context = _worker_context or context
        context = _worker_context
    return _guarded_search(dsn, context, query)


class LiveAgent:
    def __init__(self, dsn, redis_url, qdrant_url, cache=None, bug=None, worker="in-process"):
        global _worker_context
        _worker_context = None
        self.run_id = uuid.uuid4().hex[:12]
        self.tenants = [f"acme-{self.run_id}", f"globex-{self.run_id}"]
        self.names = {self.tenants[0]: "Acme Health", self.tenants[1]: "Globex Finance"}
        self.dsn, self.redis_url, self.cache_mode, self.bug, self.worker = dsn, redis_url, cache, bug, worker
        self.adapter = PostgresAdapter(dsn)
        self.adapter.initialize()
        self.guard = TenantGuard(self.adapter)
        self.redis = redis.Redis.from_url(redis_url, socket_timeout=10, socket_connect_timeout=5)
        self.redis.ping()
        self.queue = Queue(f"probe-{self.run_id}", connection=self.redis)
        self.fail_flag = f"probe:{self.run_id}:fail-first"
        self.count = 0
        self.keys = []
        self.qdrant = QdrantClient(url=qdrant_url, timeout=10) if cache and cache.startswith("qdrant") else None
        self.collection = f"probe_{self.run_id}"
        if cache == "redis-guarded":
            self.redis_cache = RedisCache(self.redis, namespace=f"probe-{self.run_id}", model_version="v1")
        elif cache == "qdrant-unscoped":
            self.qdrant.create_collection(self.collection, vectors_config=models.VectorParams(
                size=DIMENSION, distance=models.Distance.COSINE))
        elif cache == "qdrant-guarded":
            self.semantic_cache = SemanticCache(QdrantRetriever(self.qdrant, self.collection, DIMENSION, create=True),
                                                namespace=f"probe-{self.run_id}", model_version="v1")

    def close(self):
        with psycopg.connect(self.dsn) as connection:
            connection.execute("DELETE FROM grd_records WHERE tenant_id = ANY(%s)", (self.tenants,))
        for key in self.redis.scan_iter(f"*{self.run_id}*"):
            self.redis.delete(key)
        for key in self.keys:
            self.redis.delete(key)
        if self.qdrant:
            self.qdrant.delete_collection(self.collection)
            self.qdrant.close()
        self.redis.close()

    def sign_in(self, tenant):
        return _context(tenant)

    def seed(self, session, canary):
        self.count += 1
        with tenant_scope(session):
            self.guard.put(f"doc-{self.count}", {"text": canary.text}, metadata={"kind": "doc"})

    def _cached(self, session, query):
        mode = self.cache_mode
        if mode == "redis-unscoped":
            key = f"answers:{hashlib.sha256(query.encode()).hexdigest()}"
            self.keys.append(key)
            raw = self.redis.get(key)
            return (json.loads(raw) if raw else None), lambda hits: self.redis.set(key, json.dumps(hits), ex=300)
        if mode == "redis-guarded":
            with tenant_scope(session):
                hit = self.redis_cache.get(query)
            def store(hits):
                with tenant_scope(session):
                    self.redis_cache.set(query, hits)
            return (hit.data if hit else None), store
        vector = _embed(query)
        if mode == "qdrant-unscoped":
            points = self.qdrant.query_points(self.collection, query=vector, limit=1, with_payload=True,
                                              score_threshold=SIMILAR).points
            def store(hits):
                self.qdrant.upsert(self.collection, [models.PointStruct(id=str(uuid.uuid4()), vector=vector,
                                                                        payload={"hits": hits})], wait=True)
            return (points[0].payload["hits"] if points else None), store
        if mode == "qdrant-guarded":
            with tenant_scope(session):
                hit = self.semantic_cache.get(vector, min_score=SIMILAR)
            def store(hits):
                with tenant_scope(session):
                    self.semantic_cache.set(query, vector, hits)
            return (hit.data if hit else None), store
        return None, lambda hits: None

    def search(self, session, query, **unexpected):
        if unexpected:
            raise ValueError("Unexpected tool arguments")
        hits, store = self._cached(session, query)
        if hits is None:
            hits = _guarded_search(self.dsn, session, query)
            store(hits)
        return hits

    def ask(self, session, message):
        hits = self.search(session, message)
        return {"answer": hits[0] if hits else "No documents found.", "tool_calls": [{"tool": "search", "hits": hits}]}

    def call_tool(self, session, name, arguments):
        return self.search(session, **arguments)

    def submit_job(self, session, name, arguments):
        arguments = dict(arguments)
        tenant_argument = arguments.pop("tenant_id", None)
        if tenant_argument and self.bug != "tenant-argument":
            raise ValueError("Unexpected job arguments")
        payload = {"dsn": self.dsn, "bug": self.bug, "query": arguments["query"], "tenant": session.tenant_id,
                   "tenants": self.tenants, "tenant_argument": tenant_argument, "fail_flag": self.fail_flag}
        job = self.queue.enqueue(summarise, payload, retry=Retry(max=1), result_ttl=600, failure_ttl=600,
                                 meta={"owner": session.tenant_id})
        return job.id

    def run_jobs(self, fail_first=False):
        self.redis.set(self.fail_flag, "1" if fail_first else "0", ex=600)
        if self.worker == "in-process":
            SimpleWorker([self.queue], connection=self.redis).work(burst=True, logging_level="WARNING")
            return
        # A real `rq worker` process, which forks a work horse for each job.
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent), os.environ.get("PYTHONPATH", "")])}
        subprocess.run([sys.executable, "-m", "rq.cli", "worker", "--burst", "--url", self.redis_url,
                        "--logging_level", "WARNING", self.queue.name], env=env, check=True, timeout=120)

    def job_result(self, session, handle):
        job = Job.fetch(handle, connection=self.redis)
        if self.bug != "unbound-results" and job.meta["owner"] != session.tenant_id:
            raise PermissionError("Not your job")
        return job.return_value(refresh=True)
