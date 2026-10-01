# Project Assistant v0.8.18 — User Guide

This guide describes the behaviour that is actually implemented in **v0.8.18**. The coding workflow is now intentionally direct: **Chat** for investigation and **Implement** for autonomous investigation plus complete human-applied code output; ordinary code generation has no approval checkpoint. It covers setup, projects and source repositories, indexing and retrieval, conversation persistence and consolidation, local state, MCP credential storage, the CLI, the local API, and troubleshooting.

> **MCP status:** v0.8.8 connects authenticated MCP servers to the normal retrieval planner through an explicit local per-server tool allowlist. Only tools you approve in the Connections tab or with `mcp-allow` are exposed to chat retrieval. MCP tool descriptions, schemas and results are treated as untrusted external evidence, and obvious mutation-oriented tool names are blocked locally even if the server labels them read-only.

## 1. What Project Assistant is

Project Assistant is a local-first, read-only project intelligence layer. It is designed to:

- keep project conversations as local Markdown;
- index multiple source repositories/directories;
- combine exact/lexical retrieval, Chroma semantic retrieval and a deterministic knowledge graph;
- use deterministic retrieval/direct live reads for Implement mode, with the bounded planner only as a fallback;
- compile high-signal context for GPT-5.6 through the configured Portkey gateway;
- maintain consolidated long-term conversation memory;
- author source-grounded copy-pasteable code/config/tests in Implement mode mode while keeping source writes under human control.

Its normal execution path does **not** edit registered source repositories or expose an arbitrary shell.

```text
Project Assistant
  understand / retrieve / investigate / design
  retrieve all tightly coupled implementation context needed for the requested change
  author complete code/config/tests for the current coherent unit
                    |
                    v
                  Human
  review / copy-paste / run / test / commit
```

## 2. Requirements

Backend:

- Python 3.11 or later;
- Git for Git-backed projects/source repositories and Git retrieval tools;
- access to the configured Portkey gateway;
- an approved chat model and embedding route.

Frontend:

- Node.js/npm capable of running Next.js 15;
- a local browser.

macOS Keychain credential storage uses the built-in `/usr/bin/security` utility and does not add a Python dependency.

## 3. Install or upgrade

From the extracted project directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Install/update the frontend dependencies:

```bash
cd web
npm install
cd ..
```

The package installs two executable entry points:

```text
project-assistant
project-assistant-api
```

To confirm the CLI is installed:

```bash
project-assistant --help
```

## 4. Environment configuration

The backend reads configuration from environment variables. `.env.example` is a reference file; the application does not itself load `.env` files.

### Portkey

| Variable | Purpose | Default |
|---|---|---|
| `PORTKEY_BASE_URL` | Enterprise Portkey/OpenAI-compatible base URL | empty |
| `PORTKEY_API_KEY` | Portkey API key | empty |
| `PORTKEY_CHAT_MODEL` | Chat model name | `gpt-5.6` |
| `PORTKEY_EMBEDDING_MODEL` | Embedding model name | empty |
| `PORTKEY_CHAT_VIRTUAL_KEY` | Optional chat virtual key | unset |
| `PORTKEY_EMBEDDING_VIRTUAL_KEY` | Optional embedding virtual key | unset |
| `PORTKEY_CHAT_CONFIG_ID` | Optional chat config ID | unset |
| `PORTKEY_EMBEDDING_CONFIG_ID` | Optional embedding config ID | unset |
| `PORTKEY_EXTRA_HEADERS_JSON` | Optional JSON object of extra enterprise headers | `{}` |
| `PORTKEY_API_MODE` | `chat_completions` or `responses` | `chat_completions` |
| `PORTKEY_REASONING_EFFORT` | `low`, `medium`, or `high` | `high` |
| `PORTKEY_EMBEDDING_TIMEOUT_SECONDS` | Maximum seconds for one embedding HTTP request | `60` |

Example:

```bash
export PORTKEY_BASE_URL='https://your-portkey-gateway.example/v1'
export PORTKEY_API_KEY='...'
export PORTKEY_CHAT_MODEL='gpt-5.6'
export PORTKEY_EMBEDDING_MODEL='@bedrock-au/amazon.titan-embed-text-v2:0'
export PORTKEY_REASONING_EFFORT='high'
export PORTKEY_EMBEDDING_TIMEOUT_SECONDS='60'
```

Test both routes before doing a large index:

```bash
project-assistant embedding-test
project-assistant chat-test
```

### Retrieval/context

| Variable | Purpose | Default |
|---|---|---|
| `CONTEXT_MAX_TOKENS` | Maximum compiled-context budget | `48000` |
| `RETRIEVAL_AGENT_ENABLED` | Enable deterministic Implement fast path + bounded retrieval-planner fallback | `1` |
| `RETRIEVAL_AGENT_MAX_ACTIONS` | Maximum tool actions used by the retrieval planner | `6` |
| `RETRIEVAL_AGENT_MAX_ROUNDS` | Maximum retrieval-planning rounds for normal Chat | `3` |
| `IMPLEMENT_FAST_READ_FILES` | Maximum likely target files live-read directly before any Implement planner fallback | `6` |
| `IMPLEMENT_RECENT_MAX_CHARS` | Maximum recent substantive conversation text supplied to Implement mode | `8000` |
| `IMPLEMENT_RECENT_MAX_ENTRIES` | Maximum recent substantive user/assistant entries supplied to Implement mode | `6` |
| `IMPLEMENT_CONTEXT_TOKENS` | Normal Implement-mode compiled-context budget | `22000` |
| `IMPLEMENT_CONTEXT_LARGE_TOKENS` | Larger cross-repo/multi-file Implement context budget | `32000` |
| `RETRIEVAL_IMPLEMENT_FALLBACK_MAX_ACTIONS` | Maximum actions in the optional Implement planner fallback | `8` |
| `RETRIEVAL_IMPLEMENT_FALLBACK_MAX_ROUNDS` | Maximum optional Implement planner fallback rounds | `1` |

Boolean settings treat `0`, `false`, `no`, and `off` as disabled.

### Local API/workspace

| Variable | Purpose | Default |
|---|---|---|
| `PROJECT_ASSISTANT_HOME` | Global Project Assistant state directory | `~/.project-assistant` |
| `PROJECT_ASSISTANT_PROJECTS_ROOT` | Location of managed projects | `$PROJECT_ASSISTANT_HOME/projects` |
| `PROJECT_ASSISTANT_HOST` | FastAPI bind host | `127.0.0.1` |
| `PROJECT_ASSISTANT_PORT` | FastAPI port | `8000` |
| `PROJECT_ASSISTANT_ALLOW_NON_LOOPBACK` | Required opt-in for a non-loopback API bind | `0` |
| `PROJECT_ASSISTANT_BACKEND_URL` | URL used by the Next.js server-side proxy | `http://127.0.0.1:8000` |
| `PROJECT_ASSISTANT_TOKEN_FILE` | Override local API-token file path | `$PROJECT_ASSISTANT_HOME/api-token` |
| `PROJECT_ASSISTANT_API_TOKEN` | Explicit local API token instead of file-backed token | unset |

### Conversation-memory maintenance

| Variable | Purpose | Default |
|---|---|---|
| `MEMORY_CONSOLIDATION_ENABLED` | Run the background due-check while the API is running | `1` |

The consolidation interval itself is a **project setting** in `.assistant/project.json` (`consolidation_interval_hours`, default `24`), not an environment variable.

### MCP credential storage

| Variable | Purpose | Default |
|---|---|---|
| `PROJECT_ASSISTANT_MCP_AUTH_STORE` | MCP credential backend: `auto`, `keychain`, or `file` | `auto` |

On macOS, `auto` uses Keychain. The explicit file fallback is described in [MCP credential storage](#10-mcp-credential-storage-current-v081).

## 5. Starting the application

The simplest development/local start is:

```bash
./scripts/dev.sh
```

This starts:

```text
FastAPI    http://127.0.0.1:8000
Next.js    http://127.0.0.1:3000
```

The script removes Portkey credential variables from the frontend process. The browser communicates with Next.js, and the Next.js server-side proxy calls FastAPI using the private local API token.

You can also start the pieces manually:

```bash
project-assistant-api
```

and, in a second terminal:

```bash
cd web
npm run dev
```

Useful frontend commands are:

```bash
npm run dev
npm run build
npm run start
npm run typecheck
```

The backend health endpoint is intentionally unauthenticated:

```bash
curl http://127.0.0.1:8000/health
```

All `/api/*` routes require the `x-project-assistant-token` header. The normal web UI handles this through its server-side proxy; browser JavaScript does not receive the token.

## 6. Projects and source repositories

Project Assistant separates a **project workspace** from the **source roots** it reads.

A project owns:

- its configuration;
- conversation history;
- indexes and graph;
- generated memory;
- context-debug snapshots.

A source root is an independent repository or directory that the project can read/index.

Recommended shape:

```text
~/.project-assistant/projects/mainframe-platform/   # managed Project Assistant project
~/work/mainframe/ansible/                           # registered source
~/work/mainframe/java/                              # registered source
~/work/mainframe/config/                            # registered source; Git optional
```

Register each independent child repository rather than registering a parent directory that contains nested Git repositories.

### Managed project

A managed project is created underneath `PROJECT_ASSISTANT_PROJECTS_ROOT`, initialised as a Git repository, and discovered automatically from disk.

```bash
project-assistant project-create "Mainframe Platform"
```

### Imported project

An imported project is an existing Git repository that remains in its original location. Project Assistant stores only a pointer to it in `~/.project-assistant/imports.json`.

```bash
project-assistant project-import ~/work/my-existing-repo
project-assistant project-import ~/work/my-existing-repo --name "My Project"
```

For imported code repositories, the assistant prompt and project-memory file are created inside `.assistant/` so importing the repository does not create tracked-looking files in its root.

### Local `init`

`init` initialises Project Assistant state directly in the path passed with `--project`:

```bash
project-assistant --project /path/to/workspace init --name "Mainframe Platform"
```

This is different from `project-create`: it does not create/register a managed workspace through `WorkspaceRegistry`.

### Source roots

Add an external source to an initialised project:

```bash
project-assistant --project /path/to/project source-add ~/work/mainframe/ansible
project-assistant --project /path/to/project source-add ~/work/mainframe/java --name java
```

Both Git-backed and plain directories are supported.

There is currently **no CLI `source-remove` command**. Source removal is available through the UI/API (`DELETE /api/projects/{project_id}/sources/{source_name}`).

## 7. Indexing and retrieval

Run incremental indexing with:

```bash
project-assistant --project /path/to/project index
```

Indexing maintains several complementary stores:

```text
.assistant/chroma/                  semantic vectors
.assistant/lexical.sqlite3          SQLite FTS5 lexical/exact retrieval
.assistant/knowledge_graph.sqlite3  deterministic knowledge graph
.assistant/repo_catalog.json        repository-routing metadata
.assistant/index_manifest.json      file fingerprint/index manifest
.assistant/index_status.json        latest indexing status
```

Incremental indexing avoids re-embedding unchanged content. v0.8.18 also persists each indexed file's `size + mtime_ns`, so normal unchanged checks do not reread/SHA-256 hash every file on every run. Registered Git sources can use the previously indexed commit plus current committed/staged/unstaged/untracked changes to migrate older manifests to this fast path without hashing every tracked file. Git branch/HEAD metadata can still be refreshed independently of embeddings.

Normal chat/context retrieval can combine:

- exact identifiers/paths/symbols;
- FTS5 lexical search;
- Chroma vector search;
- graph expansion;
- repository routing;
- live read-only file/Git retrieval through the bounded retrieval planner.

The live retrieval toolset includes controlled operations corresponding to:

```text
search_project / search_exact
find_symbol / find_references
list_files / find_files / grep_project
file_metadata / read_file / read_file_range
git_status / git_diff / git_log / git_show
```

These operations are restricted to the project and registered source roots and are not an arbitrary shell.

### Indexing status and errors

The UI/API exposes `.assistant/index_status.json`. Full indexing started from the browser is a backend-owned job: the POST returns immediately, status transitions through `queued` → `running` → `completed` (or `failed`), and the browser polls only while the job is active. While running, status now reports an explicit phase (`scanning`, `indexing`, `finalising`) plus `processed / total` files; the `chunks` number is only newly indexed chunks and should not be treated as overall progress. The status file includes a run ID, owner PID and update timestamp and is written atomically.

Embedding HTTP calls are bounded by `PORTKEY_EMBEDDING_TIMEOUT_SECONDS` (60 seconds by default). A gateway call that never responds therefore becomes a per-file embedding failure/local-only result rather than holding the entire indexing job in `running` forever.

All indexing writers for one project share the same project-wide lock, including delayed conversation maintenance from older/rebuilt assistant instances. If the API restarts and finds an orphaned `queued`/`running` state with no live owner, it marks that run interrupted rather than leaving the browser polling forever.

If a generated memory file cannot be indexed after it is written, the error is appended to:

```text
.assistant/index_errors.log
```

Security/egress events can be recorded in:

```text
.assistant/security_events.log
```

### Implement mode

Use Implement mode when you want Project Assistant to produce code for you to apply manually. It uses the same project RAG/live-read system as normal chat, but the default delivery contract is now **complete the requested change in the current response whenever practical**.

For a clear implementation request:

1. deterministic hybrid RAG identifies likely repositories/files without a GPT planning call;
2. Project Assistant directly live-reads the most likely target files in a batch-oriented fast path;
3. when those live reads provide usable evidence, GPT-5.6 immediately produces the complete copy-pasteable implementation;
4. only when local evidence is inadequate, or the request explicitly needs external/live context such as Atlassian/CEB/Zowe, one bounded retrieval-planner fallback round is used;
5. imports, helpers, call sites, configuration, tests and documentation required for the same behaviour remain together;
6. it stops before implementation only when a material ambiguity would produce incompatible designs, required source cannot be retrieved, or the request is exceptionally large;
7. after the code it summarises the change and provides validation commands/checks.

Implement mode also uses a deliberately smaller conversation window (normally the last 2–3 substantive exchanges), filters short legacy approval/continuation chatter, and does not pull raw historical conversation RAG unless the user explicitly asks to recall earlier chat. Its context budget is adaptive: about 22k tokens for ordinary work and about 32k for larger cross-repo/multi-file changes by default.

The active runtime, retrieval planner, CLI help and browser guidance all use this same contract. Older user-memory text referring to `smallest viable implementation` or incremental micro-steps does not override it.

For each changed artefact, responses should include:

```text
Repository: <registered repository name>
File: <relative/path>
Action: Create file | Replace file | Replace function/class/section | Insert after/before <exact anchor>
Why: <short explanation>
```

Replacement code should be complete and copy-pasteable. Implement mode forbids placeholders such as `...`, `existing code`, `rest unchanged`, omitted imports or pseudo-code inside replacement blocks. Diffs are only produced when explicitly requested.

The UI has **Copy response** on assistant/event messages and a **Copy** button on fenced code blocks.


## 8. Conversation persistence

Every conversation is a Markdown file under:

```text
.assistant/conversations/
```

A typical filename is:

```text
2026-09-26-investigate-certificates-a1b2c3d4e5.md
```

The file contains YAML-like frontmatter with a `conversation_id`, followed by timestamped Markdown sections such as:

```markdown
## User · 2026-09-26T09:20:00.000000+00:00

Why did this job fail?

## Assistant · 2026-09-26T09:20:14.000000+00:00

...
```

Implement-mode generated code and ordinary chat turns remain in the same conversation Markdown, so the project history stays readable without a database viewer.

Conversation files use private file permissions.

### Concurrent conversations

v0.8.13 keeps different conversations concurrent and also frees the current conversation immediately after its persisted assistant reply is rendered. Post-response indexing is background maintenance and does not keep the composer in Working state. v0.8.12 introduced the underlying cross-conversation concurrency. You can start a response in one conversation, switch to another conversation, and send another request while the first response continues. Running conversations are marked **Running…** in the sidebar, and each conversation keeps its own stream text/error/context state.

When you select a conversation in the sidebar, the chat view jumps to the latest message as soon as that conversation has loaded. User prompts are visually distinct from assistant output with a blue-toned, right-aligned bubble, making the most recent prompt/response boundary easier to find in long conversations.


### Fast final rendering

During generation, assistant output is intentionally shown as lightweight raw text so token streaming stays responsive. When generation completes, the backend now sends the exact persisted assistant entry in the SSE `done` frame and the browser renders only that new Markdown message. It no longer reloads and reparses the complete conversation after every response. Conversation embedding/index maintenance runs after the durable Markdown write on a background worker, so indexing latency does not delay the Markdown transition.


Concurrency is deliberately per conversation:

- different conversations may retrieve and call the model at the same time;
- the same conversation may have only one active response, preventing interleaved Markdown turns;
- a duplicate request to an already-running conversation receives HTTP `409`;
- shared conversation indexing/manifest writes are serialised after responses complete;
- switching conversations/projects does not let a background completion overwrite the currently visible conversation.

This is conversation concurrency, not unrestricted project mutation concurrency. Source repositories remain read-only.

## 9. Conversation consolidation and user memory

### Why there are three kinds of conversation memory

v0.8.0 introduced a distinction between:

1. **Raw conversations** — exact historical evidence;
2. **Daily consolidations** — compressed project history that distinguishes accepted/current decisions from rejected or superseded approaches;
3. **User memory** — a compact profile of durable/repeated working and communication preferences.

This keeps abandoned ideas in old chats from behaving like current project truth merely because they are semantically similar to the latest question.

### Where the files live

All paths below are relative to the project directory unless overridden in `.assistant/project.json`.

```text
.assistant/
├── conversations/
│   └── *.md
├── generated/
│   ├── consolidations/
│   │   ├── 2026-09-26.md
│   │   └── 2026-09-26-2.md      # if more than one forced run occurs that day
│   └── user_memory.md
└── consolidation_state.json
```

The default paths come from these project settings:

```json
{
  "conversation_dir": ".assistant/conversations",
  "consolidation_dir": ".assistant/generated/consolidations",
  "user_memory_path": ".assistant/generated/user_memory.md",
  "consolidation_state_path": ".assistant/consolidation_state.json",
  "consolidation_interval_hours": 24
}
```

### What a daily consolidation contains

The consolidation prompt asks the model to retain only useful long-term project information, with sections such as:

- what was accomplished;
- current decisions / accepted approach;
- debugging and lessons learned;
- superseded or rejected approaches;
- open work / unresolved questions;
- observed user working preferences.

The generated Markdown includes metadata containing the creation time and the conversation timestamp range processed.

### What `user_memory.md` contains

`user_memory.md` is regenerated from:

```text
current user memory
        +
new daily consolidation
        ↓
complete replacement user memory
```

It is intended for durable or repeatedly supported information such as:

- engineering/problem-solving preferences;
- communication preferences;
- recurring constraints;
- stable workflow choices or goals.

Temporary task details should remain in project/consolidated history rather than user memory.

### When automatic consolidation runs

When `project-assistant-api` is running and `MEMORY_CONSOLIDATION_ENABLED` is not disabled:

1. the background loop checks every **hour**;
2. for each discovered project, it asks whether the project is due;
3. the default due interval is **24 hours** from the last attempt;
4. only conversation entries newer than `processed_through` are included;
5. if the app was not running when the interval elapsed, the next startup/hourly check catches up.

The background loop is deliberately non-fatal: a consolidation failure does not take the local UI down. Use a manual consolidation to surface a concrete error.

### Manual consolidation

Run when due:

```bash
project-assistant --project /path/to/project consolidate
```

Force a run before the 24-hour interval has elapsed:

```bash
project-assistant --project /path/to/project consolidate --force
```

The command prints JSON containing:

```text
ran
entries
reason
consolidation_path
user_memory_path
```

Common `reason` values when nothing runs are:

```text
not-due
no-new-conversation-entries
```

### Consolidation state

`.assistant/consolidation_state.json` records fields such as:

```text
last_attempt_at
last_run_at
processed_through
last_consolidation
entries_processed
```

`processed_through` is the timestamp boundary used to make consolidation incremental.

### How memory is used during context compilation

The compiler keeps assistant-generated memory separate from normal project-code RAG. Project retrieval filters out raw conversation chunks, consolidations and `user_memory.md` from the normal project-source hit set.

Compiled context has dedicated sections for:

```text
user memory
project memory
recent active conversation
consolidated history
raw conversation fallback
retrieval trace
repository routing
knowledge graph
retrieved project context
```

Raw historical conversation retrieval is a fallback. It is used when no relevant consolidation is found, or when the request explicitly asks for exact historical recall with wording such as "what did I say", "previous conversation", or "chat history".

### Disable automatic consolidation

```bash
export MEMORY_CONSOLIDATION_ENABLED=0
```

Manual `project-assistant ... consolidate` still remains available.

## 10. MCP connections and chat retrieval (v0.8.8)

MCP server configuration is global to Project Assistant rather than stored inside a project. This lets the same Atlassian, CEB or local infrastructure MCP connection be reused across multiple projects.

### Registry location

```text
~/.project-assistant/mcp-servers.json
```

or, when `PROJECT_ASSISTANT_HOME` is set:

```text
$PROJECT_ASSISTANT_HOME/mcp-servers.json
```

The registry contains only non-secret connection metadata such as server id, type, URL/command, enabled state and the local `allowed_tools` list. OAuth credentials are stored separately.

A typical remote entry looks like:

```json
{
  "id": "atlassian",
  "name": "Atlassian",
  "type": "remote",
  "enabled": true,
  "url": "https://mcp.atlassian.com/v2/mcp",
  "allowed_tools": ["getAccessibleAtlassianResources", "discover", "executeRead"]
}
```

Remote servers must use HTTPS except for loopback development URLs (`127.0.0.1`, `localhost`, `::1`). Usernames/passwords embedded in MCP URLs are rejected.

### Add a remote MCP

From the **Connections** tab, provide:

```text
id:   atlassian
name: Atlassian          # optional
url:  https://mcp.atlassian.com/v2/mcp
```

Or from the CLI:

```bash
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian
project-assistant mcp-add ceb https://YOUR-CEB-MCP-ENDPOINT/mcp --name CEB
```

The CEB URL above is intentionally a placeholder; use the exact internal endpoint from your existing CEB MCP configuration.

### OAuth behaviour

Remote MCP authentication is automatic. Project Assistant does not require you to specify an OAuth grant type, authorization endpoint, token endpoint or refresh endpoint.

On `Connect` / `mcp-connect`:

```text
Project Assistant
   -> remote MCP endpoint
   -> authentication challenge, when required
   -> MCP OAuth discovery
   -> browser opens
   -> sign in / approve
   -> http://127.0.0.1:8765/mcp/oauth/callback
   -> access/refresh/client registration state persisted
   -> original MCP connection continues
   -> tools/resources are discovered
```

The callback listener exists only during an interactive authorization flow. It binds to loopback only.

Defaults:

```bash
PROJECT_ASSISTANT_MCP_CALLBACK_PORT=8765
PROJECT_ASSISTANT_MCP_AUTH_TIMEOUT_SECONDS=300
```

If port 8765 is occupied, choose another unused loopback port before authenticating:

```bash
export PROJECT_ASSISTANT_MCP_CALLBACK_PORT=8766
```

Keep the chosen callback port stable after the first dynamic OAuth client registration. A stored client registration can include the registered redirect URI, so changing the callback port later may require `mcp-logout` followed by a fresh sign-in.

The MCP SDK handles normal access-token refresh automatically when the server issued a refresh token. If refresh is no longer possible, the next connection will require browser sign-in again.

### Credential storage

Default on macOS:

```bash
PROJECT_ASSISTANT_MCP_AUTH_STORE=auto
```

OAuth state is stored as a generic password in macOS Keychain:

```text
service: com.project-assistant.mcp.<normalised-server-id>
account: oauth
label:   Project Assistant MCP: <server-id>
```

The stored JSON bundle includes two logical objects:

```text
tokens       access token / refresh token / expiry / scope
client_info  OAuth dynamic-client registration metadata
```

Persisting `client_info` as well as tokens avoids unnecessarily registering a new OAuth client every time Project Assistant restarts.

Require Keychain explicitly:

```bash
export PROJECT_ASSISTANT_MCP_AUTH_STORE=keychain
```

Development/non-macOS fallback:

```bash
export PROJECT_ASSISTANT_MCP_AUTH_STORE=file
```

which stores the OAuth bundle at:

```text
~/.project-assistant/mcp-auth.json
```

with mode `0600`.

### Connection status and discovery

`Connect` performs a real MCP connection and records the in-process status as one of:

```text
not_checked
connected
auth_required
error
disabled
```

A successful probe records the negotiated protocol version, server identity (when supplied), instructions, tools, resources and resource templates. The Connections UI shows the discovered tool/resource counts and lets you expand the tool list.

Connection state itself is not treated as durable: after restarting Project Assistant the status starts as `not_checked`, while the OAuth credentials remain in Keychain. Pressing Connect normally completes silently when the saved credentials can still be refreshed/used.

### Local stdio MCPs

Local MCPs use the MCP SDK's stdio transport. Project Assistant launches the configured command as a subprocess when it connects and shuts it down when the connection closes.

Example CLI configuration shape:

```bash
project-assistant mcp-add-local my-local-mcp \
  /absolute/path/to/python \
  -m my_mcp_server \
  --cwd /absolute/path/to/server/project
```

This is also how the built-in Zowe MCP runs: no separate HTTP port and no manual background daemon are required.

### Built-in Zowe MCP

The Zowe server is shipped as `project_assistant_mcp.zowe`. It shells out only to fixed, read-only Zowe CLI commands and uses your existing Zowe configuration/profile. Run the registration command from a directory where direct Zowe CLI commands already work; `--cwd` matters when you use a project-level Zowe configuration.

```bash
source .venv/bin/activate
project-assistant mcp-add-local zowe project-assistant-zowe-mcp --name Zowe --cwd "$PWD"
project-assistant mcp-connect zowe
project-assistant mcp-tools zowe
```

Expected tools:

```text
zowe_info
list_datasets
list_dataset_members
read_dataset
get_job_status
get_job_spool
```

Approve them for chat retrieval explicitly:

```bash
project-assistant mcp-allow zowe \
  zowe_info \
  list_datasets \
  list_dataset_members \
  read_dataset \
  get_job_status \
  get_job_spool
```

Then test in chat with a concrete resource you are authorised to read, for example `Using Zowe, list MYHLQ.TEST.* data sets` or `Using Zowe, show the status for JOB12345`. The server never accepts arbitrary Zowe command strings. Dataset/member names and job IDs are validated, subprocesses are launched without a shell, command execution is timed out, and returned output is bounded before it enters model context.

Environment variables `ZOWE_CLI_HOME`, `NODE_EXTRA_CA_CERTS`, standard proxy variables, and SSL CA path variables are selectively forwarded to the Zowe MCP child process when they are present. Optional limits are:

```bash
ZOWE_MCP_TIMEOUT_SECONDS=60
ZOWE_MCP_MAX_OUTPUT_CHARS=60000
ZOWE_MCP_CLI_PATH=/absolute/path/to/zowe   # only if zowe is not on PATH
```

### Chat retrieval allowlist

A successful MCP connection does **not** expose every discovered tool to GPT. v0.8.8 keeps a second, local permission boundary: each server stores an explicit `allowed_tools` list. Only those exact server/tool pairs are advertised to the retrieval planner.

In the **Connections** tab:

1. connect the server and expand its discovered tools;
2. tick **Allow in chat retrieval** only for tools you want GPT to use;
3. leave write/destructive tools unticked. Obvious mutation-oriented names are disabled by Project Assistant's local policy.

CLI equivalent:

```bash
project-assistant mcp-allow atlassian getAccessibleAtlassianResources discover executeRead
project-assistant mcp-deny atlassian executeWrite executeDestructive
```

For Atlassian's current v2 gateway, a useful minimum read-only set is normally `getAccessibleAtlassianResources`, `discover`, and `executeRead`, plus any direct read/search tools you specifically want to expose. Do **not** allow `executeWrite` or destructive/mutation tools.

The retrieval planner may make multiple MCP calls in one request. This supports flows such as:

```text
user question
  -> getAccessibleAtlassianResources (resolve cloudId when needed)
  -> discover (find the relevant deferred read operation/schema)
  -> executeRead (run the discovered read operation)
  -> normalised SearchHit evidence
  -> final answer with mcp:<server>/<tool> provenance
```

MCP output from one retrieval round is visible to the next round. MCP tool calls are capped at six per user request, and returned text is bounded before it enters compiled context. Existing egress/secret checks apply to MCP results before they are sent to the configured Portkey model.

### MCP trust boundary

Do not treat an MCP server's `read_only_hint`, description, schema text, or returned content as trusted instructions. They are external evidence only. Project Assistant:

- requires a locally persisted exact-name allowlist;
- applies a defensive local block to obvious mutation-oriented tool names;
- validates that tool arguments are a JSON object;
- records the server and tool in retrieval provenance;
- does not grant permission merely because the server advertises `read_only_hint=true`.

The built-in **Zowe MCP server is now implemented**. Remaining MCP-specific work is mainly richer first-class resource/template retrieval and any future read-only Zowe capabilities we deliberately choose to add.

## 11. Complete CLI reference

Global syntax:

```bash
project-assistant [--project PROJECT] COMMAND [ARGS...]
```

`--project` is a global option and should be placed **before** the command:

```bash
project-assistant --project /path/to/project index
```

### MCP commands

MCP configuration is global, so these commands do not use `--project`.

#### `mcp-list`

List configured MCP servers and their current in-process state. After an application restart a configured server normally shows `not_checked` until it is probed again; persisted OAuth credentials are independent of that state.

```bash
project-assistant mcp-list
```

#### `mcp-add SERVER_ID URL [--name NAME] [--disabled]`

Add or update a remote Streamable HTTP MCP server.

```bash
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian
project-assistant mcp-add ceb https://YOUR-CEB-MCP-ENDPOINT/mcp --name CEB
```

HTTPS is required except for loopback development endpoints. Credentials embedded in URLs are rejected.

#### `mcp-add-local SERVER_ID COMMAND [ARGS...] [--name NAME] [--cwd PATH] [--disabled]`

Register a local stdio MCP. Project Assistant launches the command itself when connecting.

```bash
project-assistant mcp-add-local example /usr/bin/python3 -m example_mcp --cwd ~/work/example-mcp
```

#### `mcp-connect SERVER_ID`

Connect to the MCP and discover its protocol/server identity, tools, resources and resource templates. For OAuth-protected remote MCPs this can open the browser.

```bash
project-assistant mcp-connect atlassian
```

The command waits for the browser callback when interactive authorization is required.

#### `mcp-tools SERVER_ID`

Connect and print a compact list of discovered tools and resources. A server-supplied `read_only_hint` is displayed as metadata only; it is not treated as a permission boundary.

```bash
project-assistant mcp-tools atlassian
```

#### `mcp-allow SERVER_ID TOOL [TOOL...]`

Add one or more discovered tool names to the local chat-retrieval allowlist. Obvious mutation-oriented tool names are rejected even if the remote server marks them read-only.

```bash
project-assistant mcp-allow atlassian getAccessibleAtlassianResources discover executeRead
```

#### `mcp-deny SERVER_ID TOOL [TOOL...]`

Remove tools from the local chat-retrieval allowlist.

```bash
project-assistant mcp-deny atlassian executeWrite executeDestructive
```

#### `mcp-enable SERVER_ID` / `mcp-disable SERVER_ID`

Enable or disable a configured connection without deleting its configuration or OAuth state.

```bash
project-assistant mcp-disable ceb
project-assistant mcp-enable ceb
```

#### `mcp-logout SERVER_ID`

Delete locally stored OAuth tokens/client-registration state for a server while keeping the server definition. The next protected connection will require authorization again.

```bash
project-assistant mcp-logout atlassian
```

#### `mcp-remove SERVER_ID`

Remove the server definition and its locally stored OAuth state.

```bash
project-assistant mcp-remove atlassian
```

### Project discovery and lifecycle

#### `projects`

List discovered managed/imported projects.

```bash
project-assistant projects
```

Output columns:

```text
project_id    kind    name    path
```

#### `project-create NAME`

Create a managed local Git-backed project under `PROJECT_ASSISTANT_PROJECTS_ROOT`.

```bash
project-assistant project-create "Mainframe Platform"
```

Prints the project path.

#### `project-import PATH [--name NAME]`

Import an existing Git repository without copying/moving it.

```bash
project-assistant project-import ~/work/platform
project-assistant project-import ~/work/platform --name "Platform"
```

Prints the project path.

#### `project-rename PROJECT_ID NAME`

Change the display name without moving the project/repository.

```bash
project-assistant project-rename abc123def4567890 "Platform Assistant"
```

#### `project-convert-to-source PROJECT_ID TARGET_PROJECT_ID [--name NAME]`

Convert a mistakenly imported project into a registered source of another project.

```bash
project-assistant project-convert-to-source OLD_PROJECT_ID TARGET_PROJECT_ID --name ansible
```

This is metadata-only: it does not copy, move or delete the source repository. Existing `.assistant` metadata in the former imported repository is deliberately left in place.

#### `project-forget PROJECT_ID`

Forget an **imported** project without deleting its repository.

```bash
project-assistant project-forget abc123def4567890
```

Managed projects are not deleted by this command.

### Initialisation and source registration

#### `init --name NAME`

Initialise Project Assistant state in the directory selected by `--project`.

```bash
project-assistant --project ~/work/context-builder init --name "Context Builder"
```

#### `source-add PATH [--name NAME]`

Register another readable/indexable source root.

```bash
project-assistant --project ~/work/context-builder source-add ~/work/ansible --name ansible
```

### Indexing/model connectivity

#### `index`

Incrementally index changed project/source files and refresh supporting retrieval metadata.

```bash
project-assistant --project ~/work/context-builder index
```

Prints indexing result JSON.

#### `embedding-test`

Test the configured embedding route using a fixed non-sensitive string.

```bash
project-assistant embedding-test
```

Prints the configured model and returned vector dimensions on success.

This command does not require a project directory.

#### `chat-test`

Test the configured chat route in both non-streaming and streaming mode.

```bash
project-assistant chat-test
```

The test asks the model to return exactly `PROJECT_ASSISTANT_OK` and prints model/API/reasoning settings plus both responses.

This command does not require a project directory.

### Conversation and memory commands

#### `chat-new TITLE`

Create a new local Markdown conversation.

```bash
project-assistant --project ~/work/context-builder chat-new "Certificate investigation"
```

Prints the conversation ID and Markdown path.

#### `chat CONVERSATION_ID MESSAGE [--implement]`

Append the user message, perform conversation-aware retrieval, call the chat model, append the assistant answer, and print the answer.

Normal chat:

```bash
project-assistant --project ~/work/context-builder \
  chat a1b2c3d4e5 "Why is the certificate automation failing?"
```

Implement mode:

```bash
project-assistant --project ~/work/context-builder \
  chat a1b2c3d4e5 "Implement the certificate renewal feature" --implement
```

`--implement` switches retrieval purpose to `implementation`, uses deterministic RAG plus direct live target-file reads first, and adds the direct complete-change copy-paste code contract to the model prompt. A retrieval-planner call is only used as a bounded fallback when local evidence is inadequate or external/live MCP context is needed. Ordinary implementation does not pause for approval or continuation turns. Quote the message in the shell when it contains spaces/shell metacharacters.

#### `consolidate [--force]`

Run incremental conversation consolidation/user-memory update when due.

```bash
project-assistant --project ~/work/context-builder consolidate
project-assistant --project ~/work/context-builder consolidate --force
```


### Retrieval/debugging commands

#### `search QUERY`

Run the main indexed project search and print matched repo/path/line/symbol/channel information plus text snippets.

```bash
project-assistant --project ~/work/context-builder search "IKJEFT01"
```

#### `graph QUERY`

Search the deterministic knowledge graph and print matching nodes and neighbours.

```bash
project-assistant --project ~/work/context-builder graph "AssetLookup"
```

#### `route QUERY`

Show repository-routing scores/reasons produced from graph, vector and lexical evidence.

```bash
project-assistant --project ~/work/context-builder route "certificate renewal"
```

#### `context QUERY [--conversation CONVERSATION_ID]`

Compile the full agentic context for inspection/debugging and write the latest context snapshot.

```bash
project-assistant --project ~/work/context-builder \
  context "How does asset rebuild flow across repos?"
```

Include active conversation context:

```bash
project-assistant --project ~/work/context-builder \
  context "Why is this failing?" --conversation a1b2c3d4e5
```

The command prints the estimated token count, snapshot path and compiled context. The default snapshot is:

```text
.assistant/debug/last_context.md
```

## 12. Local API reference

The UI uses these API routes. All `/api/*` routes require the local API token header.

### MCP connections

```text
GET    /api/mcp/servers
POST   /api/mcp/servers/remote
POST   /api/mcp/servers/local
POST   /api/mcp/servers/{server_id}/enabled
POST   /api/mcp/servers/{server_id}/connect
POST   /api/mcp/servers/{server_id}/allowed-tools
POST   /api/mcp/servers/{server_id}/logout
DELETE /api/mcp/servers/{server_id}
```

`connect` can run the interactive OAuth flow and then returns discovered MCP capabilities. `allowed-tools` replaces the server's local chat-retrieval allowlist with the supplied exact tool names. The browser callback itself uses a temporary loopback listener on `PROJECT_ASSISTANT_MCP_CALLBACK_PORT`, not an unauthenticated FastAPI `/api/*` route.

### Status/projects

```text
GET    /health
GET    /api/status
GET    /api/projects
POST   /api/projects
POST   /api/projects/import
POST   /api/projects/register             compatibility route
GET    /api/projects/{project_id}
PATCH  /api/projects/{project_id}
POST   /api/projects/{project_id}/convert-to-source
DELETE /api/projects/{project_id}
```

### Sources/indexing

```text
POST   /api/projects/{project_id}/sources
DELETE /api/projects/{project_id}/sources/{source_name}
POST   /api/projects/{project_id}/index
GET    /api/projects/{project_id}/index-status
```

### Memory

```text
GET    /api/projects/{project_id}/memory/status
POST   /api/projects/{project_id}/memory/consolidate
POST   /api/projects/{project_id}/memory/consolidate?force=true
```

The memory status response includes:

```text
due
interval_hours
state
user_memory_path
consolidation_dir
```

### Conversations/chat

```text
GET    /api/projects/{project_id}/conversations
POST   /api/projects/{project_id}/conversations
GET    /api/projects/{project_id}/conversations/{conversation_id}
POST   /api/projects/{project_id}/conversations/{conversation_id}/stream
       # one active stream per conversation; returns 409 if that conversation is already running
```

The stream request body is:

```json
{
  "message": "...",
  "mode": "chat"
}
```

`mode` may be `chat` or `implement` (`guided` is accepted only as a legacy alias). Implement mode uses implementation-specific live retrieval and the copy-paste code contract described above.

### Retrieval inspection

```text
POST   /api/projects/{project_id}/context
POST   /api/projects/{project_id}/search
POST   /api/projects/{project_id}/graph
```

## 13. Persistent state reference

### Global state

Default global home:

```text
~/.project-assistant/
```

Typical contents:

```text
~/.project-assistant/
├── api-token                    local FastAPI API token (private)
├── imports.json                 pointers to imported projects
├── mcp-servers.json             global MCP server definitions (no secrets)
├── mcp-auth.json                only when MCP auth store=file
└── projects/                    managed projects
```

A legacy `registry.json` can be migrated by the workspace registry.

### Project-local state

Typical project-local state:

```text
<project>/
├── .assistant/
│   ├── project.json
│   ├── conversations/
│   ├── generated/
│   │   ├── consolidations/
│   │   └── user_memory.md
│   ├── consolidation_state.json
│   ├── chroma/
│   ├── lexical.sqlite3
│   ├── knowledge_graph.sqlite3
│   ├── repo_catalog.json
│   ├── index_manifest.json
│   ├── index_status.json
│   ├── debug/
│   │   └── last_context.md
│   ├── index_errors.log          when needed
│   └── security_events.log       when needed
├── assistant_system.md           managed/local init by default
└── PROJECT.md                    managed/local init by default
```

For an **imported** code repository, the prompt/project-memory paths default to:

```text
.assistant/assistant_system.md
.assistant/PROJECT.md
```

`.assistant/` is added to `.git/info/exclude` when the project directory is already a Git repository. This keeps local assistant state out of normal Git status without modifying the repository's tracked `.gitignore`.

Historical `.assistant/proposals/` and `.assistant/patches/` directories and `change_gate.py` compatibility code can exist from earlier versions. They are not wired into the normal v0.8.12 API/UI/CLI mutation path.

## 14. Project configuration (`.assistant/project.json`)

A default project configuration contains values equivalent to:

```json
{
  "name": "Project name",
  "source_roots": [],
  "system_prompt_path": "assistant_system.md",
  "project_memory_path": "PROJECT.md",
  "conversation_dir": ".assistant/conversations",
  "generated_dir": ".assistant/generated",
  "consolidation_dir": ".assistant/generated/consolidations",
  "user_memory_path": ".assistant/generated/user_memory.md",
  "consolidation_state_path": ".assistant/consolidation_state.json",
  "consolidation_interval_hours": 24,
  "chunk_size": 1600,
  "chunk_overlap": 240,
  "vector_top_k": 24,
  "lexical_top_k": 36,
  "rag_top_n": 12,
  "graph_top_n": 12,
  "graph_expansion_top_n": 8,
  "repo_route_top_n": 3
}
```

Configured internal paths are validated so a modified `project.json` cannot redirect assistant internal reads/writes outside the project directory.

Source roots can be absolute or project-relative. Existing source roots are validated before use.

## 15. Security model

### Local API

- FastAPI binds to loopback by default.
- Binding to a non-loopback address is refused unless `PROJECT_ASSISTANT_ALLOW_NON_LOOPBACK=1`.
- `/api/*` requires the generated API token.
- Next.js attaches the token server-side.

### Filesystem/source access

- registered source roots are validated;
- symlink escapes outside allowed roots are rejected;
- read-only retrieval is constrained to the project and registered source roots;
- retrieved code/text is treated as untrusted evidence rather than instructions.

### Egress

- sensitive credential paths/files are excluded;
- high-confidence secrets are blocked from outbound chat/embedding content;
- some non-egressable content can remain usable in local lexical/graph retrieval;
- Portkey credentials remain backend-only.

### MCP connections and credentials

- Keychain is preferred automatically on macOS;
- credentials are not stored in `.assistant/project.json`, `mcp-servers.json` or conversations;
- the file fallback is explicit and private-permissioned;
- remote MCP URLs require HTTPS unless they are loopback-only;
- OAuth callback handling binds only to `127.0.0.1`;
- MCP tool annotations, schemas and returned content are treated as untrusted evidence rather than authorization or instructions;
- model-driven MCP calls are limited to exact tool names in the local per-server `allowed_tools` list;
- obvious mutation-oriented tool names are blocked locally even if a server marks them read-only;
- MCP calls carry server/tool provenance, bounded output and the same egress/secret gate used by other model-bound context.

## 16. Troubleshooting

### Project Assistant starts but no projects appear

Check discovery:

```bash
project-assistant projects
```

Managed projects must exist beneath `PROJECT_ASSISTANT_PROJECTS_ROOT`. Imported projects must still exist at the path stored in `~/.project-assistant/imports.json`.

### Portkey chat fails

Run:

```bash
project-assistant chat-test
```

Then verify:

```text
PORTKEY_BASE_URL
PORTKEY_API_KEY
PORTKEY_CHAT_MODEL
PORTKEY_CHAT_VIRTUAL_KEY / PORTKEY_CHAT_CONFIG_ID if required
PORTKEY_API_MODE
PORTKEY_REASONING_EFFORT
```

### Embeddings fail

Run:

```bash
project-assistant embedding-test
```

Then verify the embedding model/virtual key/config ID and that the gateway accepts the `/embeddings` route.

### Consolidation did not create a file

Run:

```bash
project-assistant --project /path/to/project consolidate
```

If it returns `not-due`, use `--force` only when you intentionally want another run before the interval.

If it returns `no-new-conversation-entries`, the state boundary is already at or beyond the latest parseable conversation entry.

Inspect:

```text
.assistant/consolidation_state.json
.assistant/index_errors.log
```

### Automatic consolidation appears not to run

Confirm the API is running and `MEMORY_CONSOLIDATION_ENABLED` is not disabled. The background loop checks hourly, not immediately after every message.

You can inspect status via:

```text
GET /api/projects/{project_id}/memory/status
```

or manually run the CLI consolidation command.

### Retrieval is using an old conversation idea

The normal project RAG path excludes conversation/generated-memory chunks. Check:

1. whether the idea was promoted into a daily consolidation as an accepted/current decision;
2. whether it is correctly recorded under a superseded/rejected section;
3. `.assistant/debug/last_context.md` using the `context` CLI command.

### Keychain backend errors

On macOS, confirm:

```bash
/usr/bin/security help >/dev/null
```

If you deliberately need the portable fallback:

```bash
export PROJECT_ASSISTANT_MCP_AUTH_STORE=file
```

If Keychain works but MCP authentication still fails, run `project-assistant mcp-connect SERVER_ID` from a terminal so the concrete OAuth/network error is visible. If the browser cannot return to Project Assistant, check that `PROJECT_ASSISTANT_MCP_CALLBACK_PORT` is unused and that loopback traffic is not blocked.

### UI cannot call the backend

Confirm both services are running and the backend URL is correct:

```bash
curl http://127.0.0.1:8000/health
```

The browser should normally use the Next.js proxy rather than calling the protected FastAPI `/api/*` routes directly.

## 17. Validation/development commands

Backend unit tests:

```bash
python -m unittest discover -s tests -v
```

Frontend type-check:

```bash
cd web
npm run typecheck
```

Frontend production build:

```bash
cd web
npm run build
```

## 18. Current v0.8.18 limitations / next layer

Authenticated **tool-based** MCP retrieval is implemented. The remaining MCP work is narrower:

```text
MCP connectivity + retrieval (implemented)
  ├── remote Streamable HTTP + OAuth/Keychain
  ├── corporate TLS compatibility when explicitly enabled
  ├── local stdio lifecycle
  ├── capability discovery
  ├── local per-server chat tool allowlists
  ├── multi-round model-planned MCP tool calls
  └── SearchHit normalisation + provenance/output/egress controls

remaining
  └── richer first-class MCP resource/template retrieval where useful
```

A connected server still grants the model **no tool access until you explicitly approve tools**. This is deliberate: authentication and authorization-to-use-in-chat are separate controls.


## MCP connection troubleshooting

v0.8.4 flattens nested TaskGroup errors. For Atlassian use `https://mcp.atlassian.com/v2/mcp`, log out, then reconnect.


## Corporate TLS compatibility for remote MCPs

Python 3.13+ enables `VERIFY_X509_STRICT` by default. Some managed enterprise TLS interception certificates are trusted by macOS but are older/non-conforming enough to fail with errors such as `Missing Authority Key Identifier`. For those servers only, re-add/update the MCP with:

```bash
project-assistant mcp-add atlassian https://mcp.atlassian.com/v2/mcp --name Atlassian --tls-compat
project-assistant mcp-logout atlassian
project-assistant mcp-connect atlassian
```

The Connections screen exposes the same **Corporate TLS compatibility** option. This mode still requires a trusted certificate and valid hostname. It only removes Python's `VERIFY_X509_STRICT` flag; it never sets `verify=False`. Prefer having the corporate PKI issue RFC 5280-compliant certificates when that is practical.

### Conversation 404 / interrupted stream recovery

Conversation Markdown lives under `.assistant/conversations/`. In v0.8.9, a damaged front-matter ID can be recovered from the generated filename suffix, and the browser will move away from a genuinely missing conversation instead of retrying it indefinitely. New conversation headers are committed atomically and completed appends are flushed to disk.

If a conversation fails to load, inspect the directory directly:

```bash
ls -lt .assistant/conversations
find .assistant/conversations -maxdepth 1 -type f -name '*.md' -print
```

If the 404 message contains a conversation ID, look for the file containing that ID in its filename:

```bash
find .assistant/conversations -maxdepth 1 -type f -name '*-CONVERSATION_ID.md' -print
```

If the file exists, v0.8.9 can normally recover it even when the `conversation_id:` front-matter line is damaged. If the file itself no longer exists, Project Assistant cannot reconstruct unsaved text from that missing file; create/select another conversation and restore from version control or another copy if one exists.

