# Local Project Assistant — v0.7.9

Project Assistant is a local-first **read-only project intelligence layer** for source-heavy engineering work. It keeps persistent project conversations and multi-repository knowledge locally, retrieves the right code/configuration context, and uses the configured enterprise Portkey routes for GPT-5.6 reasoning and embeddings.

Its boundary is deliberate:

```text
Project Assistant
  understand / retrieve / investigate / design
  prepare a source-grounded implementation brief
                    |
                    v
                 OpenCode
  edit / run commands / test / iterate / commit
```

Project Assistant does **not** edit registered source repositories, stage/apply patches, or expose arbitrary shell execution.

## v0.7.9 — OpenCode handoff

The chat composer now has two modes:

- **Chat** — ask project questions, debug integrations, trace behaviour, discuss architecture/design and investigate failures.
- **OpenCode brief** — package the current conversation into a structured implementation handoff. You can optionally type a narrower focus, or leave the box blank to use the current conversation.

The generated brief is persisted in the conversation Markdown and includes, where supported by retrieved evidence:

- problem / desired outcome;
- current understanding and root cause;
- integration path across repositories;
- relevant repositories, relative paths and symbols;
- ordered implementation direction without a patch;
- constraints and behaviours to preserve;
- validation/tests;
- unresolved checks for OpenCode;
- retrieval evidence.

Each generated **OpenCode Implementation Brief** has a **Copy for OpenCode** button.

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
  +-- managed/imported project discovery
  +-- context compiler
  +-- SQLite FTS5 exact/lexical retrieval
  +-- deterministic knowledge graph
  +-- Chroma semantic retrieval
  +-- bounded multi-round retrieval planner
  +-- controlled live read-only filesystem/Git tools
  |
  v
Configured enterprise Portkey gateway
  +-- GPT-5.6 inference
  +-- approved embedding model
```

## Retrieval

Normal chat and OpenCode handoffs automatically perform conversation-aware retrieval. **Compile context** is an inspection/debugging feature; it is not required before asking a question.

The retrieval path combines:

```text
current request + recent conversation
        |
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

Live file/Git tools operate only inside the Project Assistant workspace and explicitly registered source roots. They do not expose an arbitrary shell and do not write to source repositories.

For **OpenCode brief** mode, the retrieval planner is specifically instructed to locate likely implementation/integration files, follow cross-repository dependencies, read important files live where possible, and return concrete repository/path/symbol references for the coding agent.

## Projects and source roots

A Project Assistant project owns its conversations/settings/indexes. Source repositories can live anywhere allowed by the source-root security policy and remain independent repositories/directories.

Recommended layout:

```text
~/.project-assistant/projects/mainframe-platform/   # Project Assistant workspace
~/work/mainframe/ansible/                           # registered source
~/work/mainframe/java/                              # registered source
~/work/mainframe/config/                            # registered source, Git optional
```

Register each independent child source rather than making a parent folder into a nested Git repository.

Both Git-backed and plain source directories are supported.

## Indexing

**Reindex changed files** is incremental:

- unchanged file content is not re-embedded;
- branch/HEAD metadata can refresh independently of content fingerprints;
- unsuitable archives/compiled/binary/generated/oversized/sensitive artefacts are skipped;
- provider-rejected or non-egressable content can remain available to local lexical/graph retrieval where appropriate;
- indexing status and skip/local-only reasons are persisted and shown in the UI.

The confirmed embedding route shape remains a direct HTTP equivalent of:

```text
POST $PORTKEY_BASE_URL/embeddings
x-portkey-api-key: $PORTKEY_API_KEY
Content-Type: application/json

{
  "model": "@bedrock-au/amazon.titan-embed-text-v2:0",
  "input": "text"
}
```

## Security boundary

### Browser/API isolation

- FastAPI binds to loopback by default.
- Every `/api/*` request requires a generated local API token.
- Next.js adds that token server-side; browser JavaScript does not receive it.
- Portkey credentials remain backend-only.

### Filesystem and egress controls

- source roots must pass registration validation;
- symlink escapes outside registered roots are rejected;
- sensitive credential paths/files are excluded;
- high-confidence secret material is blocked from outbound embedding/chat content;
- local-only retrieval can remain available when egress is not permitted;
- retrieved source is treated as untrusted evidence.

### Source mutation

The active v0.7.9 API/UI/CLI has no source patch staging/apply workflow. Registered source repositories are read through controlled tools only. Historical `change_gate.py` code/data is retained for compatibility with older project state but is not wired into the normal v0.7.9 execution path.

## Persistent local state

Project-local assistant state lives under `.assistant/`, including the semantic/lexical/graph indexes, index metadata/status, context debug snapshots and conversation Markdown. Managed/imported project discovery state lives under `~/.project-assistant/`.

## Frontend performance

The v0.7.6 performance work remains in place:

- composer text is isolated from page-level React state;
- historical Markdown messages are memoised;
- streamed output is buffered rather than re-rendering per tiny token fragment;
- compiled context is collapsed by default;
- off-screen historical messages use browser content visibility.

macOS Dictation works directly in the normal textarea; Project Assistant has no microphone/Whisper integration.

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

Configure the required Portkey environment variables in your shell, then start the backend and frontend using your existing workflow or `scripts/dev.sh`.

No reindex is required solely for the v0.7.9 UI/handoff change. Reindex only when source/index metadata itself needs refreshing.

## CLI

Useful commands include:

```bash
project-assistant projects
project-assistant --project /path/to/project index
project-assistant --project /path/to/project chat-new "Investigation"
project-assistant --project /path/to/project chat <conversation-id> "Why is this failing?"
project-assistant --project /path/to/project handoff <conversation-id>
project-assistant --project /path/to/project handoff <conversation-id> --focus "Fix the ANSWER integration issue"
project-assistant --project /path/to/project context "How does asset rebuild flow across repos?"
project-assistant --project /path/to/project search "IKJEFT01"
project-assistant --project /path/to/project graph "AssetLookup"
```

`handoff` prints the generated OpenCode implementation brief and stores it in the conversation.
