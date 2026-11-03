# Project instructions

Read `CLAUDE.md` before changing this repository. Its language, commit and
sequential 1.1.x versioning rules apply.

## Agent integration

The application must let the user configure a command to run on their machine,
with editable Codex and Claude presets and a custom command option. This follows
the installed-CLI approach requested by the user, rather than requiring a single
embedded model provider. MCP access and the in-app command runner must share the
same archive operations and filtering semantics.

### Reconstructable tests and efficient tools

- Never pause capture in the personal store for UI acceptance tests. Launch a
  separate synthetic archive with `--storage-path` and `--no-capture`. UI tools
  may stall beyond their requested timeout; do not tie restoration of a real
  user's recording state to a tool returning. If a different task explicitly
  needs a temporary pause, install and verify an independent expiry/watchdog
  before pausing and preserve the previous recording state.

- Keep native UI acceptance isolated from the user's desktop: disable test
  fullscreen transitions and global shortcut registration, and use Chromium
  focus emulation in a noninteractive window. A transparent fullscreen window
  can still hide the Dock. Test fullscreen itself only when explicitly needed.

- Assign each agent run an identifier and preserve its execution trace locally:
  selected preset, executable and redacted arguments, query, dataset/time scope,
  public tool calls and normalized filters, result identifiers and counts,
  pagination, errors, timings, output sizes, exit code, cancellation and timeout.
  Keep credentials out of traces and private traces out of Git.
- Preserve the externally observable conversation/tool events needed to explain
  what the agent actually did. Do not claim access to hidden model reasoning.
- Test through the public MCP interface and normal configured CLI/app entry
  points. Internal-function probes are diagnostic evidence, not substitutes for
  product acceptance. Preserve failed commands as well as successful ones and
  check whether returned screenshots actually satisfy the request.
- Evaluate tool suitability, not just answer plausibility: look for unnecessary
  calls, repeated reads, excessive image/OCR transfer, full-archive enumeration,
  missing filters and manual workarounds. Record the limitation and add a useful
  operation/filter when it prevents an inefficient search path.
- Provide bounded/paginated access to date/time and application filters, titles
  and OCR search, frame metadata, individual images, chronological neighborhoods
  and relevant temporal groups. Agents must be able to inspect an interval even
  when they do not yet know a useful text query. Report missing/pending OCR and
  capture gaps; never present the first observed frame as a proven event start.
- Responses must link claims to concrete screenshot identifiers/timestamps.

### Agent-proposed deletion selections

- The agent selects content; the application performs deletion. Do not expose a
  destructive MCP tool or give the configured runner a write/delete path into
  the archive. Enforce the supported runner's archive permissions, not merely
  a prompt telling the agent not to delete files.
- Allow an agent to propose individual instants and multiple time intervals.
  Resolve them into a deduplicated, explicit set of frame identifiers. Preserve
  gaps between intervals; do not expand disjoint selections into a continuous
  minimum-to-maximum interval.
- Show an app-owned deletion timeline containing only those selected frames,
  with thumbnails, dates, count and space estimate. The user must be able to
  inspect and adjust the selection before using the application's existing
  confirmation interaction.
- Bind confirmation to the reviewed selection. Revalidate it before execution;
  do not silently add newly captured or newly matching frames after preview.
  Only the app's confirmed deletion path may remove images, database rows and
  their search/index/cache references. Report any incomplete deletion.
- Classify actual visible content: a Telegram chat displayed in a browser can
  match, while an unrelated screenshot merely mentioning Telegram does not.
