# Examples

The [ten-grd-examples](https://github.com/heimdallsystems/ten-grd-examples) repository
holds runnable examples that install Ten-Grd from PyPI and use synthetic tenant
data.

| Example | Shows | Needs |
| --- | --- | --- |
| [basic/agent_memory.py](https://github.com/heimdallsystems/ten-grd-examples/blob/main/basic/agent_memory.py) | Two tenants write the same record ID; each reads only its own. | Nothing beyond Ten-Grd |
| [basic/tenant_rag.py](https://github.com/heimdallsystems/ten-grd-examples/blob/main/basic/tenant_rag.py) | A foreign document with a closer vector is excluded, and a widening selector cannot cross tenants. | In-memory Qdrant |
| [claude_agents/memory_agent.py](https://github.com/heimdallsystems/ten-grd-examples/blob/main/claude_agents/memory_agent.py) | Claude reads and writes memory through tools bound to the host's tenant. | Claude Agent SDK; API key for live mode |
| [claude_agents/rag_agent.py](https://github.com/heimdallsystems/ten-grd-examples/blob/main/claude_agents/rag_agent.py) | Claude answers from tenant-filtered retrieval. | Claude Agent SDK, in-memory Qdrant; API key for live mode |

## Run them

```sh
git clone https://github.com/heimdallsystems/ten-grd-examples.git
cd ten-grd-examples
python -m pip install -r requirements.txt
python basic/agent_memory.py
python basic/tenant_rag.py
python -m claude_agents.memory_agent
python -m claude_agents.rag_agent
```

None of these commands call a model or need an API key. The Claude examples run
their tool checks offline by default.

## Run the Claude examples live

Set `ANTHROPIC_API_KEY` in your shell (or configure another provider supported
by the Claude Agent SDK), then pass a model ID:

```sh
python -m claude_agents.memory_agent --live --model YOUR_MODEL_ID
python -m claude_agents.rag_agent --live --model YOUR_MODEL_ID
```

Each command runs two separate Claude sessions, one per synthetic tenant, with a
six-turn limit and a USD 0.50 budget per session. Live mode incurs API charges.
The [Claude examples guide](https://github.com/heimdallsystems/ten-grd-examples/tree/main/claude_agents)
explains where the tenant boundary lives and what the examples do not prove.
