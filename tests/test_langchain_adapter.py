import asyncio
import pytest
pytest.importorskip("langchain_core")
from pydantic import ValidationError
from ten_grd import InMemoryAdapter, TenantContext, TenantGuard, tenant_scope, InvalidContextError
from ten_grd.integrations.langchain import memory_tools, retrieval_tool

A = TenantContext("tenant-a", "alice", "request-a")
B = TenantContext("tenant-b", "bob", "request-b")


def test_tools_are_scoped_at_invocation_not_construction():
    guard = TenantGuard(InMemoryAdapter())
    with tenant_scope(A):
        tools = {tool.name: tool for tool in memory_tools(guard, include_writes=True)}
        tools["grd_put"].invoke({"record_id": "same", "data": "a"})
    with tenant_scope(B):
        assert tools["grd_read"].invoke({"record_id": "same"}) is None
        tools["grd_put"].invoke({"record_id": "same", "data": "b"})
    with tenant_scope(A):
        assert tools["grd_read"].invoke({"record_id": "same"}) == "a"


def test_tool_schema_rejects_forged_tenant():
    tool = memory_tools(TenantGuard(InMemoryAdapter()))[0]
    assert "tenant_id" not in tool.args
    with tenant_scope(A), pytest.raises(ValidationError):
        tool.invoke({"record_id": "same", "tenant_id": B.tenant_id})


def test_tools_require_host_identity():
    tool = memory_tools(TenantGuard(InMemoryAdapter()))[0]
    with pytest.raises(InvalidContextError):
        tool.invoke({"record_id": "same"})


def test_read_only_tools_do_not_advertise_mutations():
    tools = memory_tools(TenantGuard(InMemoryAdapter()))
    assert {tool.name for tool in tools} == {"grd_read", "grd_search"}


def test_real_async_tool_invocations_propagate_separate_contexts():
    guard = TenantGuard(InMemoryAdapter())
    tools = {tool.name: tool for tool in memory_tools(guard, include_writes=True)}
    async def run(context):
        with tenant_scope(context):
            await tools["grd_put"].ainvoke({"record_id": "same", "data": context.tenant_id})
            await asyncio.sleep(0)
            return await tools["grd_read"].ainvoke({"record_id": "same"})
    async def main():
        return await asyncio.gather(run(A), run(B))
    assert asyncio.run(main()) == [A.tenant_id, B.tenant_id]


def test_retrieval_tool_cannot_call_embedder_without_identity():
    def embed(query):
        pytest.fail("No identity: embedding must not execute")
    tool = retrieval_tool(None, embed)
    with pytest.raises(InvalidContextError):
        tool.invoke({"query": "private prompt"})
