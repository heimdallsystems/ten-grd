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
