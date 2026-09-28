# Local Project Assistant — v0.8.4

Project Assistant recreates the useful parts of the enterprise ChatGPT browser experience locally while using approved Portkey routes for GPT-5.6 inference and embeddings. It keeps persistent project conversations, indexes multiple repositories, compiles high-signal project context, maintains consolidated user/project memory, and can produce source-grounded **copy-pasteable implementation code** without writing to registered source repositories itself.

Its boundary is deliberate:

```text
Project Assistant
  understand / retrieve / investigate / design
  break larger changes into small implementation steps
  author complete code/config/tests for the current step
                    |
                    v
                  Human
  review / copy-paste / run / test / commit
```

Registered source repositories remain read-only to Project Assistant. It does not apply patches, stage files, commit, or expose arbitrary shell execution.

## User guide

For installation, environment variables, project/source management, **guided implementation**, conversation consolidation and memory locations, MCP configuration/authentication, the complete CLI/API reference, persistent-state layout and troubleshooting, see **[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)**.

## v0.8.4 — MCP diagnostics + guided implementation + memory

v0.8.3 merges the three active feature strands into one build:

- **Guided implementation** from the v0.8.0.03 branch replaces the OpenCode handoff workflow.
- **Conversation consolidation + user memory** remain from the memory-enabled v0.8.0 branch.
- **Authenticated MCP connectivity** remains from v0.8.2.

The composer now has two modes:

- **Chat** — project questions, debugging, architecture, design and investigation.
- **Guided implementation** — source-grounded coding with a human approval/write boundary.

For a non-trivial coding request, Guided implementation:

1. performs conversation-aware RAG plus bounded live read-only exploration;
2. breaks the work into 2–6 small coherent steps;
3. explains the approach and stops before implementation code;
4. waits for the user to confirm the next step;
5. re-reads the relevant live source for that turn;
6. emits complete copy-pasteable code for **one step only**;
7. gives validation commands/checks and stops again for user input.

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
  +-- bounded multi-round retrieval planner
  +-- controlled live read-only filesystem/Git tools
  +-- MCP registry + OAuth/Keychain + capability discovery
  |
  v
Configured enterprise Portkey gateway
  +-- GPT-5.6 inference
  +-- approved embedding model
```

## Retrieval

Normal Chat and Guided implementation automatically perform conversation-aware retrieval. **Compile context** remains an inspection/debugging feature and is not required before asking a question.

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
        |
        v
compiled high-signal project context
```

Guided implementation has a stronger retrieval contract: likely target files must be located and read live where possible before the model declares context sufficient for copy-paste code generation. Short continuation turns such as `yes`, `next`, or `do step 2` use recent conversation state to recover the target files.

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

## MCP connectivity

v0.8.3 includes:

- remote **Streamable HTTP** MCP connections;
- automatic OAuth discovery/browser sign-in through the MCP Python SDK;
- OAuth token and client-registration persistence in **macOS Keychain** by default;
- silent token refresh when supported by the server;
- local **stdio** MCP configuration and subprocess lifecycle;
- UI/CLI connection state plus tool/resource/template discovery.

Example remote servers:

```bash
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian
project-assistant mcp-connect atlassian

project-assistant mcp-add ceb https://YOUR-INTERNAL-CEB-MCP/mcp --name CEB
project-assistant mcp-connect ceb
```

Example local stdio server:

```bash
project-assistant mcp-add-local zowe python -m project_assistant_mcp.zowe --name Zowe
project-assistant mcp-connect zowe
```

**Current MCP boundary:** connection/authentication/discovery are implemented, but MCP tools are not yet exposed to the chat retrieval agent automatically. The next safety layer is a local per-server read-only allowlist + normalisation of MCP results into retrieval evidence.

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
python -m pip install --force-reinstall --no-deps .
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

No full reindex is required solely for the v0.8.3 workflow merge.

## CLI examples

```bash
project-assistant projects
project-assistant --project /path/to/project index
project-assistant --project /path/to/project consolidate
project-assistant --project /path/to/project chat-new "Investigation"
project-assistant --project /path/to/project chat <conversation-id> "Why is this failing?"
project-assistant --project /path/to/project chat <conversation-id> "Implement the next step" --guided
project-assistant --project /path/to/project context "How does asset rebuild flow across repos?"

project-assistant mcp-list
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian
project-assistant mcp-connect atlassian
project-assistant mcp-tools atlassian
```

The complete command-by-command reference is in [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md#11-complete-cli-reference).
