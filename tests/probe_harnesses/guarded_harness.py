"""Probe harness for the guarded toy agent."""
from toy_agents import GuardedAgent

AGENT = GuardedAgent()
TENANTS = ["acme", "globex"]
TENANT_NAMES = {"acme": "Acme Health", "globex": "Globex Finance"}
TOOLS = {"search": {"query": "{text}"}}


def sign_in(tenant):
    return AGENT.sign_in(tenant)


def seed(session, canary):
    AGENT.ingest(session, canary.text)


def ask(session, message):
    return AGENT.ask(session, message)


def call_tool(session, name, arguments):
    return getattr(AGENT, name)(session, **arguments)
