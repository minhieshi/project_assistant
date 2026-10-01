# v0.8.18

## 0.8.18 — Implement fast path and context cleanup

- Makes deterministic hybrid RAG + direct live target-file reads the default Implement path.
- Skips GPT retrieval-planner calls entirely for ordinary repo implementation when usable live targets are found.
- Keeps one bounded planner fallback round for inadequate local evidence or explicit external/live MCP needs such as Atlassian/CEB/Zowe.
- Limits Implement retrieval-query fanout to four queries and semantic embedding searches to two.
- Adds compact `recent_substantive_text` conversation context (6 entries / 8k chars by default) and filters short legacy approval/continuation chatter.
- Excludes raw historical conversation RAG from normal implementation requests unless explicit history recall is requested.
- Adds adaptive Implement context budgets: 22k tokens normally, 32k for larger cross-repo/multi-file work, bounded by `CONTEXT_MAX_TOKENS`.
- Preserves broader multi-round retrieval and larger context behaviour for normal Chat.

# v0.8.17

## 0.8.17 — Direct Implement mode

- Replaces the user-facing Guided workflow with **Implement** mode.
- Removes the ordinary chat **Approve** button and approval-turn prompt semantics; copy/paste is the code-write approval boundary.
- Keeps `guided` only as a backwards-compatible API/CLI alias that normalises to Implement mode.
- Implement mode no longer stops at plans/approval checkpoints when the request is sufficiently specified.
- Uses broader batched implementation retrieval (10 actions/round, 2 rounds by default) to reduce repeated planner latency while still grounding multi-file changes.
- Aligns browser, API, CLI, retrieval planner, memory guidance and documentation to the same direct workflow contract.
- Adds regression tests that fail if staged approval semantics or the old user-facing guided mode return.

# v0.8.16

## 0.8.16 — Workflow consistency + indexing progress/performance

- Replaces remaining active micro-step wording in the retrieval planner, CLI and browser with an end-to-end guided implementation contract.
- Defaults clear implementation requests to the complete requested feature/fix/playbook change in one response when practical; approval pauses are reserved for genuine ambiguity/missing evidence or exceptionally large work.
- Makes the UI version badge use the backend `/api/status` version rather than a hard-coded string.
- Adds explicit index phases plus `processed / total` progress so a stable new-chunk count is not mistaken for a stalled run.
- Adds `PORTKEY_EMBEDDING_TIMEOUT_SECONDS` (default 60) so one embedding request cannot block an index indefinitely.
- Persists `size` + `mtime_ns` in index-manifest records and uses them as the normal unchanged-file fast path on subsequent runs.
- Uses Git commit + working-tree change detection for registered Git source repos to avoid hashing every tracked file while migrating older manifests to the stat-signature fast path.
- Adds regression tests for unchanged-file no-rehash indexing and configured embedding HTTP timeouts.

# v0.8.15

## 0.8.15 — Adaptive guided implementation units

- Replaces the rigid `2–6 small steps` guided workflow with adaptive implementation units chosen at natural feature/file/test boundaries.
- Defaults to the largest coherent reviewable unit, typically one complete feature slice, complete playbook/set, whole-file change, or tightly coupled 2–3 file change.
- Prevents micro-steps that split imports, helpers, call sites, config and tests needed for one behaviour.
- Lets clear explicit implementation requests proceed directly to code instead of forcing a planning-only approval turn.
- Keeps approval stops for genuine design choices, ambiguity, independent subsystems, validation dependencies or otherwise unwieldy responses.
- Updates implementation retrieval to gather all tightly coupled live targets needed for the coherent unit.
- Removes the `smallest viable implementation` example from memory-consolidation guidance so architectural simplicity is not confused with tiny workflow increments.

# v0.8.14

## 0.8.14 — Reliable background full-index lifecycle

- Moves browser-triggered full indexing into a backend-owned job coordinator; the HTTP POST returns immediately instead of remaining open for the full indexing run.
- Adds persisted `queued` state and UI polling that runs only while status is `queued`/`running`, then stops on `completed`/`failed`.
- Reuses one project-wide indexing lock across assistant rebuilds/invalidation so delayed conversation indexing cannot race a manual full index through separate lock instances.
- Adds run ID, owner PID and update timestamp to index status and writes status files atomically.
- Recovers orphaned `queued`/`running` states after backend restart/crash by marking them interrupted instead of polling forever.
- Prevents duplicate full-index jobs for the same project while allowing different projects to index concurrently.

# v0.8.13

## 0.8.13 — Free chat immediately after render

- Treats the SSE `done` frame as the interactive completion boundary.
- Releases the same-conversation run lease before `done`, allowing the next turn immediately after the persisted assistant reply is rendered.
- Clears the UI Working state directly from `done` instead of waiting for stream closure.
- Detaches conversation-list refresh from the send critical path.
- Debounces/coalesces post-response conversation re-indexing (10 seconds by default) so rapid turns do not queue redundant indexing jobs.
- Adds `PROJECT_ASSISTANT_CONVERSATION_INDEX_DELAY_SECONDS` (`0` disables the debounce).

# v0.8.12

## 0.8.12 — Conversation navigation polish

- Selecting a conversation now jumps directly to the bottom once that conversation has loaded, even when it has the same number of entries as the previously selected chat.
- New entries in the current conversation retain smooth follow-to-bottom behaviour.
- User prompts now use a distinct blue-toned, right-aligned bubble and accented label so prompt/assistant boundaries are easier to scan in long conversations.
- No backend retrieval, memory, MCP, or conversation persistence behaviour changed.

# v0.8.11

## 0.8.11 — Faster response finalisation and Markdown rendering

- Sends the exact persisted assistant entry in the final SSE frame so the browser can replace the raw streaming bubble immediately without refetching the entire conversation.
- Moves post-response conversation indexing off the critical response path; the durable Markdown write completes first and best-effort embedding/index maintenance runs on a single background worker.
- Preserves unchanged conversation-entry object identity across later reloads and gives `Message` a content comparator so historical Markdown is not reparsed when only a new turn changed.
- Reuses stable `react-markdown` plugin/component objects and keeps the existing off-screen `content-visibility` optimisation while avoiding unnecessary Markdown reparses on long conversations.
- Keeps the v0.8.10 conversation concurrency and scoped Approve behaviour unchanged.

# v0.8.10

## 0.8.10 — Concurrent conversations + scoped Approve button

- Allows independent conversations to stream concurrently in the web UI; each conversation keeps its own running state, streamed text, error and retrieval context.
- Keeps one active response per conversation. A second simultaneous turn in the same conversation returns HTTP 409 instead of interleaving Markdown history.
- Prevents a background conversation completion from replacing the conversation/project currently visible in the browser.
- Serialises only shared post-response indexing/manifest writes while leaving retrieval and inference concurrent across conversations.
- Makes `last_context.md` writes atomic so concurrent context compilation cannot leave a partial debug snapshot.
- Adds a visible **Approve** button for the latest assistant proposal. Approval is persisted as a normal user turn and is explicitly scoped to that latest proposal only.
- Approval does not grant permanent permissions, write access, destructive actions, or broader future scope.
- Adds concurrency regression tests; full backend suite passes.

# v0.8.9

## 0.8.9 — Conversation recovery and streaming resilience

- Makes Portkey chat streaming tolerant of metadata-only chunks where `choice.delta` or `delta.content` is `None`.
- Normalises optional/empty model output before persistence and rejects `None` conversation entries with a clear error rather than an attribute failure.
- Writes new conversation headers atomically and fsyncs appended conversation turns.
- Recovers conversation identity from the generated `*-<conversation-id>.md` filename if YAML/front matter is damaged.
- Avoids a second `stat()` race while sorting conversation files.
- Returns HTTP 404 only for a genuinely missing conversation; other read/parse failures are no longer disguised as not-found.
- Makes the browser recover from a stale/missing selected conversation by refreshing the server list and selecting a valid fallback instead of getting stuck on the dead ID.
- Adds regression coverage for damaged front matter, `None` conversation writes, and metadata-only Portkey stream chunks.

# v0.8.8

## 0.8.8 — Built-in read-only Zowe MCP

- Adds `project_assistant_mcp.zowe`, a local stdio MCP server backed by the user's existing Zowe CLI/profile configuration.
- Exposes only six fixed read-only tools: `zowe_info`, `list_datasets`, `list_dataset_members`, `read_dataset`, `get_job_status`, and `get_job_spool`.
- Never accepts arbitrary Zowe commands and never uses a shell; validates data-set/member/job identifiers before invoking the CLI.
- Bounds command time and output size and redacts obvious credential/token material from surfaced Zowe errors.
- Selectively forwards Zowe/profile/proxy/CA environment variables that the MCP SDK's deliberately minimal stdio environment would otherwise omit.
- Integrates with the existing local MCP lifecycle and v0.8.7 per-server chat retrieval allowlist; no separate daemon or port is required.
- Adds Zowe MCP setup/testing instructions and environment settings to the user guide.

# v0.8.7

## 0.8.7 — Locally-approved MCP retrieval

- Connects authenticated MCP tools to the normal multi-round retrieval planner through an explicit per-server `allowed_tools` list.
- Adds Connections-tab **Allow in chat retrieval** controls plus `mcp-allow` / `mcp-deny` CLI commands.
- Treats MCP descriptions, schemas and results as untrusted external evidence; a server `read_only_hint` is never trusted as authorization.
- Adds a defensive local blocker for obvious mutation-oriented tool names, including write/create/update/delete/destructive operations.
- Adds the planner `mcp_call` action with exact server/tool selection, JSON-object arguments and a maximum of six MCP calls per request.
- Makes MCP results from one retrieval round available to subsequent rounds, supporting workflows such as Atlassian `discover` followed by `executeRead`.
- Normalises MCP results into `SearchHit` evidence with `mcp:<server>/<tool>` provenance, bounded output and the normal egress/secret safety gate.
- Preserves OAuth/Keychain, corporate TLS compatibility, optional capability discovery, guided implementation and conversation memory/consolidation.

# v0.8.6

## 0.8.6 — MCP optional discovery compatibility

- Keeps a successful `tools/list` result when an MCP server returns JSON-RPC `-32601 Method not found` for optional resource discovery methods.
- Treats `resources/list` and `resources/templates/list` as independently optional during probing instead of failing the whole MCP connection.
- Adds nested ExceptionGroup-aware detection for `Method not found` without weakening failures for `tools/list`, authentication, TLS, or other transport errors.

# v0.8.5

## 0.8.5 — Corporate TLS compatibility for MCP

- Adds per-server `tls_compat` for managed enterprise TLS interception chains rejected by Python 3.13+ `VERIFY_X509_STRICT`, including `Missing Authority Key Identifier`.
- Compatibility mode uses the operating-system trust store and keeps `CERT_REQUIRED` plus hostname verification; it does **not** use `verify=False`.
- Adds `project-assistant mcp-add ... --tls-compat` and a matching Connections UI checkbox.
- Improves the MCP diagnostic message to recommend compatibility mode only for this specific strict-X.509 failure.

# v0.8.4

## 0.8.4 — MCP connection diagnostics and transport hardening

- Flattens nested `ExceptionGroup`/AnyIO TaskGroup failures so MCP CLI/UI errors show the underlying HTTP/OAuth cause instead of only `unhandled errors in a TaskGroup`.
- Explicitly advertises OAuth authorization-code + refresh-token grant types and `response_types=["code"]` during dynamic client registration.
- Applies the MCP SDK recommended Streamable HTTP timeout profile (30s connect/write/pool, 300s read) when using the custom OAuth-enabled `httpx2.AsyncClient`.
- Redacts authorization headers and OAuth callback/token query values from surfaced diagnostics.
- Keeps guided implementation, consolidated memory, and MCP credential/registry behaviour unchanged.

# v0.8.3

## 0.8.3 — Guided implementation merged with memory and MCP

- Merges the v0.8.0.03 guided-implementation branch into the memory/MCP line.
- Replaces the active OpenCode implementation-brief workflow with **Guided implementation**.
- Adds `--guided` to `project-assistant chat` and removes the active `handoff` command/API/UI path.
- Larger implementation requests are decomposed into small steps and paused before code generation; confirmed steps re-read likely target files live.
- GPT may author complete source/config/test code for human copy-paste, with repository/path/action metadata and no ellipsis/placeholders inside replacement units.
- Adds response-level and fenced-code copy controls in the web UI.
- Preserves the v0.8.0 incremental conversation consolidation and `user_memory.md` layer unchanged.
- Preserves v0.8.2 remote Streamable HTTP OAuth/Keychain connectivity and local stdio MCP lifecycle/capability discovery.
- Registered source roots remain read-only; Project Assistant still does not apply patches, run builds/tests or mutate Git state.

# v0.8.2

## 0.8.2 — Authenticated MCP connectivity

- Adds the global MCP server registry at `~/.project-assistant/mcp-servers.json` with remote Streamable HTTP and local stdio server types.
- Adds MCP Python SDK v2 as a runtime dependency.
- Adds automatic OAuth discovery/browser authorization for remote MCPs, using a short-lived loopback callback listener and the SDK's PKCE/token-refresh flow.
- Persists both OAuth token state and OAuth client-registration metadata through the existing macOS Keychain/private-file credential abstraction.
- Adds real MCP capability discovery: negotiated protocol/server identity, tools, resources and resource templates.
- Adds the Connections UI for adding remote MCP URLs, connecting/reconnecting, enabling/disabling, logging out and removing connections.
- Adds `mcp-list`, `mcp-add`, `mcp-add-local`, `mcp-connect`, `mcp-tools`, `mcp-enable`, `mcp-disable`, `mcp-logout` and `mcp-remove` CLI commands.
- Adds MCP API routes for remote/local registration, connection probing, enable/disable, logout and removal.
- Keeps model-driven MCP tool execution disabled until a local per-server read-only allowlist/provenance layer is implemented; server `read_only_hint` annotations are displayed but not trusted as authorization.
- Corrects the internal enterprise MCP name in documentation/examples to **CEB**.

# v0.8.1

## 0.8.1 — MCP credential foundation on v0.8.0 memory

- Builds on v0.8.0 without changing the consolidated conversation memory layer.
- Adds a reusable MCP credential-store abstraction for upcoming remote MCP connectivity.
- Uses the native macOS Keychain by default on macOS; OAuth bundles are stored as generic-password items and never written to project configuration.
- Stores access token, refresh token, expiry and client-registration metadata together as opaque JSON for OAuth refresh/re-authentication flows.
- Adds an explicit private-file fallback (`PROJECT_ASSISTANT_MCP_AUTH_STORE=file`) for development/non-macOS use; the file is restricted to mode 0600.
- Keeps credential lookup failures separate from project/retrieval state so future remote MCP connections can surface `authentication required` cleanly.
- Adds `docs/USER_GUIDE.md` with the complete CLI/API reference, memory/consolidation file layout, environment settings, persistent-state map, troubleshooting and an explicit statement of the current MCP-connectivity boundary.
- Expands `.env.example` to document all current retrieval, API, memory and MCP credential-store environment settings.

# v0.8.0

## 0.8.0 — Consolidated conversation memory

- Added incremental 24-hour conversation consolidation using only entries since the previous successful consolidation.
- Added generated daily consolidation Markdown and derived user-memory Markdown.
- Context compilation now prefers user memory + consolidated history and filters raw conversation/generated memory out of normal project-code RAG.
- Raw historical chat remains indexed only as a fallback for exact-history recall or when no consolidation exists.
- Added manual CLI/API consolidation and memory status endpoints.
- Added an in-process hourly due check; actual model consolidation is limited to once per configured 24-hour interval unless manually forced.
- Added tests for incremental consolidation, 24-hour due logic and memory-aware context retrieval.

# v0.7.9

## 0.7.9 — Read-only project intelligence + OpenCode handoff

- Pivots Project Assistant away from source mutation and into read-only project intelligence.
- Removes proposal approval, patch staging, exact-diff approval and apply routes from the public API/UI.
- Adds **OpenCode brief** mode in the composer. It can use the current conversation as the focus or accept an optional handoff focus.
- Adds multi-round handoff retrieval that locates concrete repositories, relative paths, symbols, integration boundaries, configuration and tests, with live reads where possible.
- Generates a structured, source-grounded implementation brief and stores it in the conversation Markdown.
- Adds **Copy for OpenCode** directly on generated implementation briefs.
- Keeps project/source registration, indexing, RAG, graph retrieval, live read-only filesystem/Git inspection, context inspection and chat unchanged.
- Source repositories are not modified by Project Assistant; OpenCode remains responsible for edits, shell commands, builds, tests and Git changes.

# v0.7.8

## 0.7.8 — Authoritative live Git change context

- Change-mode retrieval now receives live repository state for **all** registered source roots, independent of RAG hits or repo routing.
- Git-backed sources expose current branch, HEAD and clean/dirty working-tree state; non-Git sources are explicitly marked Git-not-applicable.
- Before declaring a change plan sufficient, the retrieval planner is instructed to read likely target files live and use `git_show(HEAD, path)` when the committed version matters.
- Proposal generation no longer treats diff staging/hash generation as a model responsibility; the backend performs staging, SHA-256 binding and HEAD verification only after plan approval.
- The project repo is now included in the registered writable roots alongside external source repos.
