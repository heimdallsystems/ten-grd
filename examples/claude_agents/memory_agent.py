"""Claude Agent SDK memory tools backed by Ten-Grd. Default: no model calls."""
import asyncio

from claude_agent_sdk import tool
from ten_grd import GuardError, InMemoryAdapter, TenantContext, TenantGuard, tenant_scope

from .common import arguments, demo_context, payload, reply, run_agent, schema, validate_strings


def make_tools(memory, context):
    if type(context) is not TenantContext:
        raise ValueError("A host-validated TenantContext is required")

    @tool("read_memory", "Read one record from the current tenant's memory.",
          schema({"record_id": {"type": "string", "minLength": 1, "maxLength": 128}}))
    async def read_memory(args):
        try:
            validate_strings(args, {"record_id": 128})
            # Bind scope in the callback, not just around client.query(): SDK
            # callbacks may execute in another task with a different context.
            with tenant_scope(context):
                record = memory.get(args["record_id"])
            return reply({"found": record is not None,
                          "data": record.data if record is not None else None})
        except (ValueError, GuardError):
            return reply({"error": "Memory operation denied"}, error=True)

    @tool("write_memory", "Save a short note in the current tenant's memory.", schema({
        "record_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
    }))
    async def write_memory(args):
        try:
            validate_strings(args, {"record_id": 128, "text": 2000})
            # The host grants this demo user write access. Real hosts must check
            # per-action permissions, or omit this tool for read-only users.
            with tenant_scope(context):
                memory.put(args["record_id"], {"text": args["text"]})
            return reply({"saved": True})
        except (ValueError, GuardError):
            return reply({"error": "Memory operation denied"}, error=True)

    return [read_memory, write_memory]


def seed_memory():
    memory = TenantGuard(InMemoryAdapter())
    for tenant in ("acme", "other"):
        with tenant_scope(demo_context(tenant)):
            memory.put("private-plan", {"text": f"{tenant} synthetic private plan"})
    return memory


async def local_check(memory):
    acme = make_tools(memory, demo_context("acme"))
    other = make_tools(memory, demo_context("other"))
    # Direct callback checks use real Ten-Grd and Agent SDK tool objects, no LLM.
    a, b = await asyncio.gather(
        acme[0].handler({"record_id": "private-plan"}),
        other[0].handler({"record_id": "private-plan"}),
    )
    assert payload(a)["data"]["text"] == "acme synthetic private plan"
    assert payload(b)["data"]["text"] == "other synthetic private plan"
    override = await acme[0].handler({"record_id": "private-plan", "tenant_id": "other"})
    assert override["is_error"]
    saved = await acme[1].handler({"record_id": "new-note", "text": "Acme only"})
    assert not saved["is_error"]
    assert not payload(await other[0].handler({"record_id": "new-note"}))["found"]
    print("PASS: tenant-scoped reads/writes; tenant override rejected. No Claude call made.")


async def main():
    args = arguments(__doc__)
    memory = seed_memory()
    await local_check(memory)
    if args.live:
        for tenant in ("acme", "other"):
            print(f"\nHost-bound tenant: {tenant}")
            await run_agent(make_tools(memory, demo_context(tenant)),
                            "Read private-plan, then save a short summary under summary. "
                            "Read summary back and show it to me.", model=args.model)


if __name__ == "__main__":
    asyncio.run(main())
