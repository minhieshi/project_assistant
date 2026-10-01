# Local Project Assistant — v0.8.18

Project Assistant recreates the useful parts of the enterprise ChatGPT browser experience locally while using approved Portkey routes for GPT-5.6 inference and embeddings. It keeps persistent project conversations, indexes multiple repositories, compiles high-signal project context, maintains consolidated user/project memory, and can produce source-grounded **copy-pasteable implementation code** without writing to registered source repositories itself.

Its boundary is deliberate:

```text
Project Assistant
  understand / retrieve / investigate / design
  retrieve all tightly coupled implementation context
  author the complete requested change in the current response when practical
                    |
                    v
                  Human
  review / copy-paste / run / test / commit
```

Registered source repositories remain read-only to Project Assistant. It does not apply patches, stage files, commit, or expose arbitrary shell execution.

## User guide

For installation, environment variables, project/source management, **Implement mode**, conversation consolidation and memory locations, MCP configuration/authentication, the complete CLI/API reference, persistent-state layout and troubleshooting, see **[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)**.

## v0.8.18 — Implement fast path

v0.8.18 removes most agentic orchestration from ordinary coding turns. **Implement** now runs deterministic hybrid retrieval first, live-reads the most likely target files directly, and goes straight to GPT-5.6 when those reads provide usable implementation evidence. A GPT retrieval-planner call is now a fallback rather than a mandatory pre-answer step; external/live requests such as Atlassian/CEB/Zowe can still use one fallback round.

Implement-mode conversation context is intentionally smaller and cleaner: by default only the last six substantive user/assistant entries (up to 8,000 characters) are included, and short legacy approval/continuation chatter is filtered. Raw historical conversation RAG is excluded from normal implementation requests unless the user explicitly asks to recall prior chat history.

Compiled implementation context is adaptive rather than always allowing the full 48k-token ceiling: ordinary changes default to about **22k tokens**, while larger cross-repo/multi-file work can grow to about **32k tokens**. General Chat keeps the broader context/retrieval behaviour.

## v0.8.17 — Direct Implement mode

v0.8.17 removes the staged Guided/Approve interaction model from normal coding. The browser now has **Chat** and **Implement** only. Implement mode investigates autonomously, uses broader batched retrieval with at most two planner rounds by default, and produces the complete requested copy-pasteable change in the current response whenever practical. The user's copy/paste is the write/approval boundary; ordinary code generation has no approval or continuation checkpoint. The API still accepts the old `guided` mode as a compatibility alias, but it is normalised immediately to Implement mode and is not exposed in the UI.

## v0.8.16 — Workflow consistency + indexing progress/performance

v0.8.16 improved the then-current guided workflow consistency and indexing diagnostics. It also made the browser version badge read from the live backend instead of being hard-coded.

Indexing reports real `processed / total` progress and explicit scanning/indexing/finalising phases. Embedding HTTP calls have a configurable timeout (`PORTKEY_EMBEDDING_TIMEOUT_SECONDS`, default 60 seconds) so one stalled gateway request cannot leave a run apparently frozen forever. Successful indexes persist `size + mtime_ns` file signatures, allowing later unchanged-file checks to avoid rereading/SHA-256 hashing every file; Git-backed registered sources can also use the previously indexed commit plus current Git changes to migrate to this fast path without hashing every tracked file.

## v0.8.15 — Adaptive Implement mode units

v0.8.15 removes the overly granular guided-workflow rule that forced non-trivial work into `2–6 small steps` and then emitted only one tiny step per approval. Guided mode now optimises for the **largest coherent, reviewable implementation unit**: typically a complete feature slice, whole file change, complete playbook/set of related playbooks, or a tightly coupled 2–3 file change including required tests/configuration.

Clear implementation requests can proceed directly to code for the first coherent unit instead of inserting a planning-only stop. Work is split only at natural boundaries such as independent subsystems, material design decisions, required validation between stages, or an otherwise unwieldy response. Coupled imports/helpers/call-sites/tests are kept together rather than becoming separate micro-steps.

The guided prompt explicitly overrides older consolidated-memory preferences that may mention the `smallest viable implementation`; memory consolidation guidance is also updated so future preferences distinguish **simple architecture** from **tiny implementation increments**.

## v0.8.14 — Reliable background full-index lifecycle

v0.8.14 fixes a stale `running` index-status failure that could leave the Project tab polling forever after indexing had stopped making progress. Full project indexing is now owned by a backend job coordinator rather than by the lifetime of the HTTP request that started it. Pressing **Reindex changed files** returns immediately with a persisted `queued` state; the UI polls only while the state is `queued` or `running` and stops automatically on `completed` or `failed`.

All assistant instances for the same project now share one project-wide index lock, including instances created after config invalidation/rebuild. This prevents delayed conversation indexing from an older assistant instance racing a manual full index against the same Chroma collection, manifest, graph and lexical index. Index status writes are atomic and include a run ID, owner PID and update timestamp. After a backend crash/restart, an orphaned `queued`/`running` status is converted to an interrupted failure instead of remaining permanently active.

## v0.8.13 — Interactive completion before indexing

v0.8.13 makes the persisted/rendered assistant reply the true end of an interactive turn. The browser clears **Working…** as soon as the final SSE `done` frame arrives, and the backend releases the same-conversation run lease before sending that frame, so the user can immediately continue in the same conversation while maintenance catches up.

Conversation re-indexing is now debounced (10 seconds by default) and coalesced per conversation file, avoiding a queue of redundant full-conversation re-index jobs during rapid back-and-forth chat. Sidebar conversation-list refresh is also detached from the send critical path. Configure the delay with `PROJECT_ASSISTANT_CONVERSATION_INDEX_DELAY_SECONDS`; set it to `0` to restore immediate background scheduling.

## v0.8.12 — Fast final render + conversation navigation polish

v0.8.12 removes two long-conversation latency traps from the chat UI. The final streamed assistant entry is now delivered in the SSE `done` frame and rendered directly, rather than refetching and reparsing the entire Markdown conversation. Post-response conversation indexing is also moved to a single background worker after the durable Markdown write, so embedding/index maintenance no longer holds the UI in its raw streaming state.

Conversation navigation is also polished in v0.8.12: selecting a conversation jumps directly to its latest message, and user prompts use a distinct blue-toned, right-aligned bubble so prompt/assistant boundaries are easy to scan in long chats.

Historical message components are content-memoised, while the existing off-screen `content-visibility` optimisation remains in place, reducing later reload/layout work for large conversations. Conversation concurrency remains unchanged; ordinary code generation no longer has an approval button or approval-turn workflow.

## v0.8.10 — Concurrent conversations + scoped approval

v0.8.10 lets separate conversations run at the same time without sharing one global browser `busy`/stream buffer. Each conversation keeps independent streaming text, retrieval context and errors, while the backend enforces one active response per individual conversation so Markdown turns cannot interleave.


No reindex is required for this release.

## v0.8.9 — Conversation recovery and streaming resilience

v0.8.9 hardens the Markdown conversation path after a failed stream/write could leave the UI pointing at an unloadable conversation:

- tolerates Portkey streaming chunks with no text delta instead of dereferencing `None.content`;
- normalises empty/optional model text safely before conversation persistence;
- creates conversation headers atomically and fsyncs appended turns;
- recovers a conversation ID from the generated filename when front matter is damaged;
- reports only genuine missing conversations as HTTP 404; parse/I/O failures now retain their real error status/message;
- if a selected conversation truly disappears, the browser refreshes the authoritative list and moves to another valid conversation instead of retrying the dead ID indefinitely.

No reindex is required for this release.

## v0.8.8 — Zowe MCP + MCP retrieval + Implement mode + memory

v0.8.8 keeps the three active feature strands together and adds the first built-in read-only Zowe MCP server:

- **Implement mode** from the v0.8.0.03 branch replaces the OpenCode handoff workflow.
- **Conversation consolidation + user memory** remain from the memory-enabled v0.8.0 branch.
- **Authenticated MCP connectivity + locally approved read-only MCP retrieval** build on v0.8.2–v0.8.7.
- **Built-in Zowe MCP** exposes a deliberately small fixed set of read-only z/OS functions through the already-configured local Zowe CLI.

The composer now has two modes:

- **Chat** — project questions, debugging, architecture, design and investigation.
- **Implement mode** — source-grounded coding with a human approval/write boundary.

For a non-trivial coding request, Implement mode:

1. performs conversation-aware RAG plus bounded live read-only exploration;
2. chooses the largest coherent reviewable unit instead of an arbitrary number of small steps;
3. keeps tightly coupled file/config/test changes together;
4. proceeds directly when the user clearly asked to implement a well-defined change;
5. pauses for approval only when a material design decision, ambiguity or natural stage boundary requires it;
6. re-reads all relevant live source for the current unit;
7. emits complete copy-pasteable code for the full coherent unit and validation commands.

For each changed artefact, the model is instructed to state:

```text
Repository: <registered repository>
File: <relative/path>
Action: Create file | Replace file | Replace function/class/section | Insert at exact anchor
Why: <short explanation>
```

Replacement code must be complete for the stated unit: no `...`, `existing code`, `rest unchanged`, omitted imports or pseudo-code inside replacement blocks. The UI provides **Copy response** and per-code-block **Copy** controls.

## Architecture

```text
Browser
  |
  v
Next.js 127.0.0.1:3000
  |  server-side proxy; local API token is not exposed to browser JS
  v
FastAPI 127.0.0.1:8000
  |
  +-- Markdown conversation history
  +-- conversation consolidation + user memory
  +-- managed/imported project discovery
  +-- context compiler
  +-- SQLite FTS5 exact/lexical retrieval
  +-- deterministic knowledge graph
  +-- Chroma semantic retrieval
  +-- deterministic Implement fast path + bounded retrieval-planner fallback
  +-- controlled live read-only filesystem/Git tools
  +-- MCP registry + OAuth/Keychain + capability discovery
  +-- local per-server MCP tool allowlists + retrieval evidence
  |
  v
Configured enterprise Portkey gateway
  +-- GPT-5.6 inference
  +-- approved embedding model
```

## Retrieval

Normal Chat and Implement mode automatically perform conversation-aware retrieval. **Compile context** remains an inspection/debugging feature and is not required before asking a question.

```text
current request + recent conversation
        |
        +-- user memory / consolidated history
        +-- exact identifiers / paths / symbols
        +-- SQLite FTS5 lexical search
        +-- semantic vector retrieval
        +-- deterministic knowledge graph
        +-- repository routing as a boost, never a hard exclusion
        |
        v
bounded GPT retrieval planner
        |
        +-- search_project / search_exact
        +-- find_symbol / find_references
        +-- list_files / find_files / grep_project
        +-- file_metadata / read_file / read_file_range
        +-- git_status / git_diff / git_log / git_show
        +-- mcp_call (only locally approved MCP tools)
        |
        v
compiled high-signal project + MCP context
```

Implement mode has a stronger retrieval contract: likely target files must be located and read live where possible before the model declares context sufficient for copy-paste code generation. Short continuation turns such as `yes`, `next`, or `do step 2` use recent conversation state to recover the target files.

## Conversation consolidation and user memory

Raw conversation Markdown remains the audit trail. Long-term conversational context is derived through incremental consolidations:

```text
.assistant/conversations/                       raw chat history
.assistant/generated/consolidations/YYYY-MM-DD.md
.assistant/generated/user_memory.md             durable working preferences
.assistant/consolidation_state.json             incremental checkpoint
```

By default the API checks hourly and consolidation runs when the configured interval is due (24 hours by default). If the app was off, the next API startup catches up when the project is due.

Manual consolidation:

```bash
project-assistant --project /path/to/project consolidate
project-assistant --project /path/to/project consolidate --force
```

The context compiler prefers current conversation + consolidated history/user memory. Raw historical chats are fallback evidence rather than normal project-code RAG.

## MCP connectivity and chat retrieval

v0.8.7 includes:

- remote **Streamable HTTP** MCP connections;
- automatic OAuth discovery/browser sign-in through the MCP Python SDK;
- OAuth token and client-registration persistence in **macOS Keychain** by default;
- silent token refresh when supported by the server;
- local **stdio** MCP configuration and subprocess lifecycle;
- UI/CLI connection state plus tool/resource/template discovery;
- a **local per-server tool allowlist** for chat retrieval;
- model-planned MCP calls across multiple retrieval rounds;
- MCP results normalised into ordinary retrieval evidence with server/tool provenance and output limits;
- a local guard that refuses obviously mutating tool names even if a server labels them read-only.

Example remote servers:

```bash
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian
project-assistant mcp-connect atlassian

# If your managed corporate proxy certificate is rejected with
# "Missing Authority Key Identifier":
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian --tls-compat
project-assistant mcp-connect atlassian

project-assistant mcp-add ceb https://YOUR-INTERNAL-CEB-MCP/mcp --name CEB
project-assistant mcp-connect ceb
```

Built-in Zowe MCP (run this from a directory where your normal `zowe` CLI configuration works):

```bash
project-assistant mcp-add-local zowe project-assistant-zowe-mcp --name Zowe --cwd "$PWD"
project-assistant mcp-connect zowe
project-assistant mcp-tools zowe
project-assistant mcp-allow zowe zowe_info list_datasets list_dataset_members read_dataset get_job_status get_job_spool
```

The built-in server exposes only `zowe_info`, `list_datasets`, `list_dataset_members`, `read_dataset`, `get_job_status`, and `get_job_spool`. It does not accept arbitrary commands and cannot submit jobs, upload, modify, rename, or delete z/OS resources.

After connecting a server, explicitly approve the tools that chat may use. In the **Connections** tab, tick **Allow in chat retrieval** only for tools you intend to be read-only. Server `read_only_hint` metadata is shown but is not trusted as authorization.

For Atlassian v2, a useful read-only set is typically `getAccessibleAtlassianResources`, `discover`, `executeRead`, and direct read/search tools you want exposed. Do **not** approve `executeWrite` or destructive/mutating tools. The retrieval planner can use `discover` in one round and `executeRead` in the next, so deferred Atlassian tools work without exposing write pathways.

CLI equivalent:

```bash
project-assistant mcp-allow atlassian getAccessibleAtlassianResources discover executeRead
project-assistant mcp-list
```

## Security boundary

- FastAPI binds to loopback by default.
- Every `/api/*` request requires a generated local API token.
- Next.js adds that token server-side; browser JavaScript does not receive it.
- Portkey credentials remain backend-only.
- MCP OAuth credentials use macOS Keychain by default.
- source roots must pass registration validation;
- symlink escapes outside registered roots are rejected;
- sensitive credential paths/files are excluded;
- high-confidence secret material is blocked from outbound embedding/chat content;
- local-only retrieval can remain available when egress is not permitted;
- retrieved source is treated as untrusted evidence.

Project Assistant may **author** code but may not **apply** it.

## Install / upgrade

From the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Install/update frontend dependencies when needed:

```bash
cd web
npm install
cd ..
```

Configure the Portkey environment variables, then start with your existing workflow or:

```bash
./scripts/dev.sh
```

No full reindex is required for the v0.8.8 Zowe MCP layer.

## CLI examples

```bash
project-assistant projects
project-assistant --project /path/to/project index
project-assistant --project /path/to/project consolidate
project-assistant --project /path/to/project chat-new "Investigation"
project-assistant --project /path/to/project chat <conversation-id> "Why is this failing?"
project-assistant --project /path/to/project chat <conversation-id> "Implement the certificate renewal feature" --implement
project-assistant --project /path/to/project context "How does asset rebuild flow across repos?"

project-assistant mcp-list
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian
project-assistant mcp-connect atlassian
project-assistant mcp-tools atlassian
project-assistant mcp-allow atlassian getAccessibleAtlassianResources discover executeRead
```

The complete command-by-command reference is in [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md#11-complete-cli-reference).

