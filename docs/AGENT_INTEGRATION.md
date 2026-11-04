# Agent integration reference

Configure Codex, Claude or a custom command in **Settings → AI & agents → Ask AI**.
The command uses its CLI login and may send retrieved content to its model provider.

## Custom commands

A custom command receives the question
on stdin and can return text or an `{"answer":"..."}` JSON object. Commands run
in a temporary working directory: use absolute paths for custom scripts.
`{mcp_config}` in an argument expands to the generated MCP configuration path;
the same path is available as `OPENRELIFE_MCP_CONFIG`. There is no shell
interpolation of the question. A response from a command that did not use any
archive tools is explicitly marked as ungrounded.

## Local MCP access

**Settings → AI & agents → MCP** displays the stdio command for external MCP
clients. Register that command in the client's MCP configuration while
OpenReLife is running.
The bridge is `python -B -m openrelife.mcp_server --connection <connection-file>`;
the desktop app maintains the private connection file automatically. Tools:

- `archive_status`: available dates, applications, counts and pending OCR.
- `find_frames`: bounded chronological listing with time, app, title, OCR text,
  any-term and OCR-state filters. A text query is not required.
- `semantic_search`: keyword/semantic ranking with time and app filters.
- `read_frames` and `view_frame`: paginated OCR or a size-limited screenshot.
- `neighboring_frames` and `find_intervals`: chronological context, capture
  gaps and groups of matching frames, for investigating observed events.
- `propose_deletion`: select individual instants or disjoint intervals. Pass an
  existing `selection_id` to add another batch to the same proposal.

Agents must distinguish an observed frame from a proven event start. Missing
captures or pending OCR mean incomplete evidence, not proof an event did not
happen. The tools expose screenshot citations as `openrelife://frame/<timestamp>`.

## Deletion stays in the app

An agent can propose candidates but cannot execute deletion through MCP. Open
**Review deletion timeline** to inspect only the proposed frames, their
thumbnails, dates, count and size. Exclude individual frames or reset/cancel the
selection, then use the app's existing confirmation button. The confirmation
is bound to the exact reviewed snapshot; newly captured or newly matching
frames are not silently included. If an agent appends to a proposal, the old
preview cannot confirm it until reloaded.

Pending proposals retain a link to the original request and its answer across
app restarts. Opening an empty panel restores the latest available context
without executing it again; **Show request** switches to a proposal's context.
**Discard proposal** removes only that proposal, keeping screenshots and search
data intact. Proposals from older versions whose context was not retained are
explicitly marked as unavailable; their original prompt is not reconstructed
from the agent's selection description.

Within the supported macOS command runner, an outer OS sandbox prevents direct
archive reads/writes, including resolved screenshot-directory symlinks. Archive
access goes through the scoped MCP API. The desktop's separate mutation
capability travels over a private pipe and is added only to the owned WebView's
requests. The HTTP interface now requires that capability outside the scoped
MCP endpoint: open the desktop UI rather than browsing the raw localhost URL.
External MCP clients retain their host's own OS permissions; registering this
server does not sandbox the entire external client.

## Traces, budgets and validation

Runs have identifiers and local JSONL traces under `agent/traces` in the data
directory. They record queries, redacted commands, public tool inputs, returned
frame identifiers, output hashes, byte counts, timings, CLI events, failures,
exit codes, cancellation and timeout. Image payloads are omitted from traces.
No hidden model reasoning is reconstructed. Source-dependent in-app answers
and traces are invalidated when their screenshots are deleted. Saved proposal
contexts retain bounded dependency maps across restarts, so unrelated deletion
does not clear them. Older traces without a known map, and contexts whose full
dependency map exceeds the limit, are conservatively invalidated on deletion.

Proposal contexts are private snapshots (not executable sessions), limited to
30 records, 2 MiB per record, 100 MiB overall and 30 days, pruned at startup and
when new contexts are created. An unfinished request is restored as interrupted
and is never automatically resumed.

The runner permits one in-app run at a time, up to 150 tool calls and about
30 MiB of tool responses per run. The default timeout is 300 seconds and can be
configured between 10 and 1800 seconds. Traces are capped per run and pruned to
100 MiB/30 days on startup; pending proposals are bounded to 20 files,
100 MiB and seven days. Local command execution currently requires macOS.

Development checks use disposable synthetic archives, never the personal
capture store:

```sh
.venv/bin/python -m pytest -q
cd electron-app
node --test tests/agents.test.cjs tests/agent-security.test.cjs
cd ..
.venv/bin/python tests/manual/check_agents_public.py --providers codex claude
```

The public test starts the normal backend with `--storage-path` and
`--no-capture`, exercises the real stdio MCP client and command runner, and
preserves failures and semantic checks under `logs/agent-integration-*`.
JavaScript tests use the product HTML and scripts in a simulated DOM; they
are not native visual acceptance. Validate the native layout on a Mac as well.
For an isolated desktop archive, the app also accepts `--storage-path=<path>`,
`--no-capture` and `--show`; `--backend-python=<absolute-path>` selects an
existing development interpreter instead of creating another environment.
