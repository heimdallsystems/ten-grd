"""Run after pip install -e .: python examples/agent_memory.py."""
from ten_grd import InMemoryAdapter, TenantContext, TenantGuard, tenant_scope

memory = TenantGuard(InMemoryAdapter())

# A real host must authenticate these subjects and validate tenant membership.
with tenant_scope(TenantContext("tenant-a", "alice", "request-a")):
    memory.put("plan", {"text": "Tenant A's private agent plan"})

with tenant_scope(TenantContext("tenant-b", "bob", "request-b")):
    assert memory.get("plan") is None
    memory.put("plan", {"text": "Tenant B's private agent plan"})
    print(memory.get("plan").data)

with tenant_scope(TenantContext("tenant-a", "alice", "request-c")):
    print(memory.get("plan").data)
