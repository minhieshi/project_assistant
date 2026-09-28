# Project Assistant v0.8.3 — User Guide

This guide describes the behaviour that is actually implemented in **v0.8.3**. It covers setup, projects and source repositories, indexing and retrieval, conversation persistence and consolidation, local state, MCP credential storage, the CLI, the local API, and troubleshooting.

> **MCP status:** v0.8.3 includes the remote/local MCP registry, Streamable HTTP and stdio connectivity, automatic OAuth browser authentication and Keychain-backed OAuth persistence. Atlassian and internal remote MCPs such as CEB can now be connected and their capabilities discovered. The chat retrieval agent does not yet execute MCP tools automatically; a local read-only allowlist is the next safety layer.

## 1. What Project Assistant is

Project Assistant is a local-first, read-only project intelligence layer. It is designed to:

- keep project conversations as local Markdown;
- index multiple source repositories/directories;
- combine exact/lexical retrieval, Chroma semantic retrieval and a deterministic knowledge graph;
- use a bounded retrieval planner for additional live read-only file/Git inspection;
- compile high-signal context for GPT-5.6 through the configured Portkey gateway;
- maintain consolidated long-term conversation memory;
- author source-grounded copy-pasteable code/config/tests in Guided implementation mode while keeping source writes under human control.

Its normal execution path does **not** edit registered source repositories or expose an arbitrary shell.

```text
Project Assistant
  understand / retrieve / investigate / design
  plan small implementation steps
  author complete code/config/tests for the confirmed step
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
python -m pip install --force-reinstall --no-deps .
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

Example:

```bash
export PORTKEY_BASE_URL='https://your-portkey-gateway.example/v1'
export PORTKEY_API_KEY='...'
export PORTKEY_CHAT_MODEL='gpt-5.6'
export PORTKEY_EMBEDDING_MODEL='@bedrock-au/amazon.titan-embed-text-v2:0'
export PORTKEY_REASONING_EFFORT='high'
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
| `RETRIEVAL_AGENT_ENABLED` | Enable bounded multi-round retrieval planner | `1` |
| `RETRIEVAL_AGENT_MAX_ACTIONS` | Maximum tool actions used by the retrieval planner | `6` |
| `RETRIEVAL_AGENT_MAX_ROUNDS` | Maximum retrieval-planning rounds | `3` |

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

Incremental indexing avoids re-embedding unchanged content. Git branch/HEAD metadata can be refreshed independently of content fingerprints.

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

The UI/API exposes `.assistant/index_status.json`.

If a generated memory file cannot be indexed after it is written, the error is appended to:

```text
.assistant/index_errors.log
```

Security/egress events can be recorded in:

```text
.assistant/security_events.log
```

### Guided implementation

Use Guided implementation when you want Project Assistant to produce code for you to apply manually. It uses the same project RAG/live-read system as normal chat, but with a stricter implementation contract.

For a non-trivial new implementation request:

1. the retrieval planner locates the likely repositories, files, integration boundaries and tests;
2. Project Assistant breaks the work into 2–6 small coherent steps;
3. it explains the plan and **stops before code**;
4. after you confirm a step (`yes`, `do step 1`, `next`, etc.), it re-runs implementation retrieval and re-reads likely target files live;
5. it produces complete pasteable code for that step only;
6. it gives validation commands/checks and stops for your result before advancing.

A genuinely small, self-contained change may be implemented immediately when your request clearly asks for the code.

For each changed artefact, responses should include:

```text
Repository: <registered repository name>
File: <relative/path>
Action: Create file | Replace file | Replace function/class/section | Insert after/before <exact anchor>
Why: <short explanation>
```

Replacement code should be a complete copy-pasteable unit. Guided mode explicitly forbids placeholders such as `...`, `existing code`, `rest unchanged`, omitted imports or pseudo-code inside replacement blocks. Diffs are only produced when explicitly requested.

The UI has **Copy response** on assistant/event messages and a **Copy** button on fenced code blocks.

CLI example:

```bash
project-assistant --project ~/work/context-builder \
  chat a1b2c3d4e5 "Implement the next step" --guided
```

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

Guided implementation plans, confirmations, generated code and ordinary chat turns remain in the same conversation Markdown, so the project history stays readable without a database viewer.

Conversation files use private file permissions.

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

## 10. MCP connections (v0.8.3)

MCP server configuration is global to Project Assistant rather than stored inside a project. This lets the same Atlassian, CEB or local infrastructure MCP connection be reused across multiple projects.

### Registry location

```text
~/.project-assistant/mcp-servers.json
```

or, when `PROJECT_ASSISTANT_HOME` is set:

```text
$PROJECT_ASSISTANT_HOME/mcp-servers.json
```

The registry contains only non-secret connection metadata such as server id, type, URL/command and enabled state. OAuth credentials are stored separately.

A typical remote entry looks like:

```json
{
  "id": "atlassian",
  "name": "Atlassian",
  "type": "remote",
  "enabled": true,
  "url": "https://mcp.atlassian.com/v2/mcp"
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

This is the mechanism intended for the Zowe MCP server: no separate HTTP port and no manual background daemon are required.

### Current safety boundary

v0.8.3 deliberately keeps MCP **connection/authentication/discovery** separate from model-driven MCP tool execution. Although the internal manager has low-level resource/tool methods, the chat retrieval agent does not yet receive MCP tools automatically.

That means:

```text
implemented now
  ✓ configure remote/local MCPs
  ✓ OAuth browser sign-in
  ✓ token/client-info persistence
  ✓ silent refresh when supported
  ✓ reconnect / logout
  ✓ list tools/resources/templates
  ✓ local stdio lifecycle

next layer
  - per-server local read-only tool allowlist
  - MCP resources/tool results -> retrieval evidence
  - model-driven MCP calls with provenance and output limits
  - Zowe MCP server exposing the approved read-only Zowe functions
```

Do not treat an MCP server's `read_only_hint` annotation as a security boundary. The UI displays it as useful metadata only; Project Assistant will still require a local allowlist before model-driven tool execution is enabled.

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

#### `chat CONVERSATION_ID MESSAGE [--guided]`

Append the user message, perform conversation-aware retrieval, call the chat model, append the assistant answer, and print the answer.

Normal chat:

```bash
project-assistant --project ~/work/context-builder \
  chat a1b2c3d4e5 "Why is the certificate automation failing?"
```

Guided implementation:

```bash
project-assistant --project ~/work/context-builder \
  chat a1b2c3d4e5 "Implement the next step" --guided
```

`--guided` switches retrieval purpose to `implementation`, requires stronger live-file grounding, and adds the stepwise copy-paste code contract to the model prompt. Quote the message in the shell when it contains spaces/shell metacharacters.

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
POST   /api/mcp/servers/{server_id}/logout
DELETE /api/mcp/servers/{server_id}
```

`connect` can run the interactive OAuth flow and then returns discovered MCP capabilities. The browser callback itself uses a temporary loopback listener on `PROJECT_ASSISTANT_MCP_CALLBACK_PORT`, not an unauthenticated FastAPI `/api/*` route.

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
```

The stream request body is:

```json
{
  "message": "...",
  "mode": "chat"
}
```

`mode` may be `chat` or `guided`. Guided mode uses implementation-specific live retrieval and the copy-paste code contract described above.

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

Historical `.assistant/proposals/` and `.assistant/patches/` directories and `change_gate.py` compatibility code can exist from earlier versions. They are not wired into the normal v0.8.3 API/UI/CLI mutation path.

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
- MCP tool annotations such as `read_only_hint` are treated as hints, not trusted authorization;
- model-driven MCP tool calls remain disabled until the local allowlist layer is added.

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

## 18. Current v0.8.3 limitations / next layer

Authenticated MCP connectivity is now present. The remaining MCP work is intentionally narrower:

```text
MCP connectivity (implemented)
  ├── remote Streamable HTTP + OAuth
  ├── local stdio lifecycle
  └── capability discovery

MCP retrieval integration (next)
  ├── local read-only tool allowlists
  ├── MCP results normalised as retrieval evidence
  ├── provenance/output-size controls
  └── read-only Zowe MCP server and approved Zowe functions
```

The separation is deliberate: successful authentication to Atlassian or CEB does not automatically grant the language model permission to invoke every tool exposed by that server.


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
