# Claude Agent SDK example validation

Historical record. The examples and their tests now live in the public
[ten-grd-examples](https://github.com/heimdallsystems/ten-grd-examples) repository.

Local draft validation on 22 October 2026, Linux / CPython 3.12.14.

- Claude Agent SDK: 0.2.172; MCP: 2.3.0; Qdrant client: 1.19.1.
- New example boundary tests: **12 passed**.
- Complete available local suite: **89 passed, 16 skipped**. Optional integration
  and live-service skips remain; this is not a full backend release gate.
- Both default example commands passed without making model calls.
- Tests used real in-memory MCP client/server dispatch for the Agent SDK tools,
  real Ten-Grd checks, and embedded Qdrant retrieval.
- Covered tenant overrides, invalid inputs, isolated reads/writes, independent
  host contexts and scope restoration, inconclusive verification, foreign backend
  results, missing context, and restricted agent configuration.
- Live Claude transport, authentication, model responses and provider costs were
  **not tested**. Live mode is opt-in through `--live --model ...`.

No production data was used. No core SDK semantics or dependencies changed.
The examples are repository files and remain separate from the wheel installed
by pip. Remote CI, publication and live-provider checks are separate steps.

## Recovery validation — 23 October 2026

Recovered the original examples and tests from the saved cloud-chat file edits
and subsequent command-based corrections, including MCP result serialization.
The recovery is based on main `63ced9c` (0.2.1); the earlier cumulative PyPI
documentation/version changes are not included in this example handoff.

Windows / CPython 3.13.11, Claude Agent SDK 0.2.172, MCP 2.3.0 and Qdrant client
1.19.1: the full local suite passed **95 tests, with 15 live-service skips**,
including all 12 example boundary tests. Both offline examples passed.
Tests ran outside the Windows sandbox after sandboxed runs stalled.

The integration CI job now installs the example dependencies and runs both
offline examples, so their boundary tests are exercised there. Live Claude
authentication, transport, model responses and provider costs remain untested;
the intended next step is a manual CLI run using `--live --model ...`.
No package version change or PyPI publication is part of this handoff.
