"""Exercise example tool boundaries with the real SDK, without a model or API key."""
import asyncio
from contextlib import asynccontextmanager
import json

import pytest

pytest.importorskip("claude_agent_sdk")
pytest.importorskip("qdrant_client")

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from qdrant_client import QdrantClient

from examples.claude_agents.common import agent_options, demo_context, payload
from examples.claude_agents.memory_agent import make_tools, seed_memory
from examples.claude_agents import rag_agent
from ten_grd import (
    InMemoryAdapter, TenantGuard, VerificationResult, VerificationStatus,
    Z3InvariantEngine, current_context, tenant_scope,
)
from ten_grd.exceptions import InvalidContextError
from ten_grd.operations import Record


@asynccontextmanager
async def mcp_client(tools):
    options = agent_options(tools, cwd="/tmp", model="not-called")
    server = options.mcp_servers["tenant"]["instance"]
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as group:
            group.start_soon(server.run, *server_streams, server.create_initialization_options())
            async with ClientSession(*client_streams) as session:
                await session.initialize()
                yield session
            group.cancel_scope.cancel()


def test_real_mcp_dispatch_rejects_override_and_keeps_writes_scoped():
    async def check():
        memory = seed_memory()
        async with mcp_client(make_tools(memory, demo_context("acme"))) as client:
            normal = await client.call_tool("read_memory", {"record_id": "private-plan"})
            assert not normal.model_dump(by_alias=True)["isError"]
            assert json.loads(normal.content[0].text)["data"]["text"] == "acme synthetic private plan"
            denied = await client.call_tool("read_memory", {
                "record_id": "private-plan", "tenant_id": "other",
            })
            assert denied.model_dump(by_alias=True)["isError"]
            assert "other synthetic private plan" not in str(denied)
            saved = await client.call_tool("write_memory", {"record_id": "new", "text": "Acme only"})
            assert not saved.model_dump(by_alias=True)["isError"]
        async with mcp_client(make_tools(memory, demo_context("other"))) as client:
            missing = await client.call_tool("read_memory", {"record_id": "new"})
            assert json.loads(missing.content[0].text) == {"found": False, "data": None}
    asyncio.run(check())


@pytest.mark.parametrize("args", [
    {}, {"record_id": 1}, {"record_id": " "}, {"record_id": "x" * 129},
    {"record_id": "private-plan", "tenant_id": "other"},
])
def test_direct_callback_rejects_malformed_arguments(args):
    tools = make_tools(seed_memory(), demo_context("acme"))
    result = asyncio.run(tools[0].handler(args))
    assert result["is_error"]
    assert "synthetic private plan" not in str(result)


def test_bound_context_overrides_ambient_scope_and_is_restored():
    async def check():
        memory = seed_memory()
        acme = make_tools(memory, demo_context("acme"))[0]
        other = make_tools(memory, demo_context("other"))[0]
        with tenant_scope(demo_context("ambient")) as ambient:
            results = await asyncio.gather(*(
                tool.handler({"record_id": "private-plan"})
                for tool in (acme, other, acme, other)
            ))
            assert [payload(r)["data"]["text"] for r in results] == [
                "acme synthetic private plan", "other synthetic private plan",
                "acme synthetic private plan", "other synthetic private plan",
            ]
            assert current_context() == ambient
        with pytest.raises(InvalidContextError):
            current_context()
    asyncio.run(check())


def test_unknown_verification_does_not_execute_adapter():
    class UnknownEngine(Z3InvariantEngine):
        def verify_operation(self, context, operation):
            return VerificationResult(VerificationStatus.UNKNOWN, operation.digest,
                                      context.policy_version, "test timeout")

    class MustNotExecute:
        def execute(self, operation):
            pytest.fail("Inconclusive verification reached the adapter")

    tools = make_tools(TenantGuard(MustNotExecute(), engine=UnknownEngine()), demo_context("acme"))
    assert asyncio.run(tools[0].handler({"record_id": "private-plan"}))["is_error"]


def test_foreign_backend_result_is_not_returned_to_claude():
    class BadAdapter:
        def execute(self, operation):
            return (Record.create("other", "private-plan", {"text": "FOREIGN_SECRET"}),)

    result = asyncio.run(make_tools(TenantGuard(BadAdapter()), demo_context("acme"))[0].handler(
        {"record_id": "private-plan"}))
    assert result["is_error"]
    assert "FOREIGN_SECRET" not in str(result)


def test_missing_host_context_cannot_create_tools():
    with pytest.raises(ValueError):
        make_tools(TenantGuard(InMemoryAdapter()), None)


def test_real_mcp_rag_excludes_foreign_document_and_denies_override():
    async def check():
        backend = QdrantClient(location=":memory:")
        try:
            documents = rag_agent.seed_documents(backend)
            await rag_agent.local_check(documents)
            async with mcp_client(rag_agent.make_tools(documents, demo_context("acme"))) as client:
                result = await client.call_tool("search_policy", {"topic": "refunds"})
                assert not result.model_dump(by_alias=True)["isError"]
                assert "30 days" in result.content[0].text
                assert "90 days" not in result.content[0].text
                denied = await client.call_tool("search_policy", {"topic": "refunds", "tenant_id": "other"})
                assert denied.model_dump(by_alias=True)["isError"]
        finally:
            backend.close()
    asyncio.run(check())


def test_agent_options_remove_other_tool_surfaces():
    options = agent_options(make_tools(seed_memory(), demo_context("acme")),
                            cwd="/tmp", model="not-called")
    assert options.tools == [] and options.strict_mcp_config
    assert options.setting_sources == [] and options.skills == [] and options.plugins == []
    assert not options.resume and not options.continue_conversation
    denial = asyncio.run(options.can_use_tool("Bash", {"command": "anything"}, None))
    assert denial.behavior == "deny"
