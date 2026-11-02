"""Two toy agents for probe tests: one with classic isolation bugs, one built on ten-grd.

Both answer with the best keyword match from their document store. No model is involved.
"""
import re

from ten_grd import Eq, InMemoryAdapter, TenantContext, TenantGuard, tenant_scope

WORD = re.compile(r"[a-z0-9]+")


def overlap(query, text):
    return len(set(WORD.findall(query.lower())) & set(WORD.findall(text.lower())))


class LeakyAgent:
    """Shared store; searches every tenant's documents; trusts tenant_id from input."""

    def __init__(self):
        self.docs = []  # (tenant, text)

    def sign_in(self, tenant):
        return {"tenant": tenant}

    def ingest(self, session, text):
        self.docs.append((session["tenant"], text))

    def search(self, session, query, tenant_id=None):
        # Bug: honours a caller-supplied tenant_id, and with none, searches everyone.
        pool = [d for d in self.docs if tenant_id is None or d[0] == tenant_id]
        return sorted((text for _, text in pool), key=lambda t: -overlap(query, t))[:1]

    def ask(self, session, message):
        # Bug: a "model" that obeys tenant_id=... in the prompt.
        found = re.search(r"tenant_id=([a-z0-9-]+)", message)
        hits = self.search(session, message, found.group(1) if found else None)
        return {"answer": hits[0] if hits else "No documents found.", "tool_calls": [{"tool": "search", "hits": hits}]}


class GuardedAgent:
    """Same behaviour, but every read and write goes through ten-grd in the session's scope."""

    def __init__(self):
        self.guard = TenantGuard(InMemoryAdapter())
        self.count = 0

    def sign_in(self, tenant):
        # Stands in for the host's authentication; the probe never builds context itself.
        return TenantContext(tenant, f"user@{tenant}", f"probe-{tenant}")

    def ingest(self, session, text):
        self.count += 1
        with tenant_scope(session):
            self.guard.put(f"doc-{self.count}", {"text": text}, metadata={"kind": "doc"})

    def search(self, session, query, **unexpected):
        if unexpected:
            raise ValueError("Unexpected tool arguments")
        with tenant_scope(session):
            records = self.guard.search(Eq("kind", "doc"))
        return sorted((r.data["text"] for r in records), key=lambda t: -overlap(query, t))[:1]

    def ask(self, session, message):
        hits = self.search(session, message)
        return {"answer": hits[0] if hits else "No documents found.", "tool_calls": [{"tool": "search", "hits": hits}]}


class CachedAgent(GuardedAgent):
    """Guarded retrieval behind an exact response cache. Keyed without the tenant unless told otherwise."""

    def __init__(self, tenant_in_key=False):
        super().__init__()
        self.tenant_in_key = tenant_in_key
        self.cache = {}

    def _key(self, session, query):
        return (session.tenant_id, query) if self.tenant_in_key else query

    def _lookup(self, key):
        return self.cache.get(key)

    def search(self, session, query, **unexpected):
        if unexpected:
            raise ValueError("Unexpected tool arguments")
        key = self._key(session, query)
        hits = self._lookup(key)
        if hits is None:
            hits = self.cache[key] = super().search(session, query)
        return hits


class SimilarCachedAgent(CachedAgent):
    """A similar-prompt cache: a close enough query counts as a hit."""

    def _lookup(self, key):
        tenant, query = key if self.tenant_in_key else (None, key)
        words = set(WORD.findall(query.lower()))
        for cached in self.cache:
            cached_tenant, cached_query = cached if self.tenant_in_key else (None, cached)
            other = set(WORD.findall(cached_query.lower()))
            if tenant == cached_tenant and len(words & other) / max(len(words | other), 1) >= 0.5:
                return self.cache[cached]
        return None


class JobAgent(GuardedAgent):
    """A background worker. `bug` selects one scope mistake; None is the correct worker."""

    def __init__(self, bug=None):
        super().__init__()
        self.bug = bug
        self.raw = []       # The same documents, as a worker with a raw client would see them.
        self.queue = []
        self.results = {}
        self.owners = {}
        self.current = None  # The last request's context, which a careless worker inherits.

    def ingest(self, session, text):
        super().ingest(session, text)
        self.raw.append((session.tenant_id, text))

    def submit(self, session, name, arguments):
        arguments = dict(arguments)
        tenant_argument = arguments.pop("tenant_id", None)
        if tenant_argument and self.bug != "tenant-argument":
            raise ValueError("Unexpected job arguments")
        handle = f"job-{len(self.owners)}"
        self.owners[handle] = session.tenant_id
        self.queue.append((handle, session, arguments["query"], tenant_argument))
        self.current = session
        return handle

    def _unscoped(self, query, tenant=None):
        pool = [text for owner, text in self.raw if tenant is None or owner == tenant]
        return sorted(pool, key=lambda t: -overlap(query, t))[:1]

    def _work(self, session, query, tenant_argument, retrying):
        if self.bug == "lost-scope" or (self.bug == "retry-lost-scope" and retrying):
            return self._unscoped(query)
        if tenant_argument:
            return self._unscoped(query, tenant_argument)
        return GuardedAgent.search(self, self.current if self.bug == "stale-context" else session, query)

    def run(self, fail_first=False):
        queued, self.queue = self.queue, []
        for handle, session, query, tenant_argument in queued:
            self.results[handle] = self._work(session, query, tenant_argument, retrying=fail_first)

    def result(self, session, handle):
        if self.bug != "unbound-results" and self.owners[handle] != session.tenant_id:
            raise PermissionError("Not your job")
        return self.results.get(handle)
