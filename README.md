# Local Project Assistant — v0.4

A local-first engineering workbench for source-heavy enterprise work: persistent Markdown conversations, multi-repo RAG, deterministic knowledge graph, context compilation and two-stage approval-gated code changes.

```text
Browser
  |
  v
Next.js 127.0.0.1:3000
  |  server-side proxy; local token never reaches browser JS
  v
FastAPI 127.0.0.1:8000
  |
  +-- Markdown conversation ledger
  +-- project/repo registry
  +-- context compiler
  +-- SQLite FTS5 exact/lexical retrieval
  +-- deterministic knowledge graph
  +-- Chroma semantic retrieval
  +-- change-control gate
  |
  v
Approved Portkey gateway
  +-- GPT-5.6 inference
  +-- approved embedding model
```

The CLI remains available and uses the same project state.

## Security model added in v0.4

### 1. Browser/API isolation

The browser no longer calls FastAPI directly.

- FastAPI requires a randomly generated local API token for every `/api/*` request.
- The token is stored at `~/.project-assistant/api-token` with user-only permissions by default.
- Next.js reads the token **server-side** and proxies requests to FastAPI.
- Browser JavaScript never receives the local API token or any Portkey credentials.
- FastAPI refuses non-loopback binding unless `PROJECT_ASSISTANT_ALLOW_NON_LOOPBACK=1` is explicitly set.
- The Next proxy rejects cross-site state-changing requests and only proxies to a loopback backend.
- `scripts/dev.sh` explicitly removes Portkey API/virtual-key/config variables from the frontend process.

### 2. Filesystem and egress controls

Before content can be sent to the Portkey embedding or chat endpoint:

- project-internal configured paths must resolve inside the registered project;
- symlinked source files that resolve outside a registered source root are ignored;
- the whole filesystem root, whole home directory and known credential directories such as `.ssh`, `.aws`, `.gnupg` and macOS Keychains cannot be registered as sources;
- obvious secret-bearing files such as `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `credentials*`, `.npmrc`, `.netrc` and kubeconfig are excluded;
- source/document content is scanned for clear credential material before remote embeddings;
- a file containing credential-like material remains available to local lexical/graph retrieval but is marked **local-only** and is not embedded remotely;
- embedding queries and compiled GPT prompts receive a final secret scan immediately before Portkey;
- retrieved source is marked as untrusted evidence in the compiled context;
- absolute graph filesystem paths are replaced by repo-relative labels before GPT sees them.

A block is recorded locally in `.assistant/security_events.log` without writing the secret value itself.

### 3. Exact-diff approval binding

Plan approval is **not** permission to modify source.

```text
change request
     |
     v
AI proposal written to conversation.md
     |
     v
PENDING PLAN APPROVAL
     |
     v
Approve plan
     |
     v
candidate diff may be prepared
     |
     v
git apply --check
copy exact diff into .assistant/patches
record SHA-256 + repo + Git HEAD
write exact diff into conversation.md
     |
     v
PENDING DIFF APPROVAL
     |
     v
Approve exact diff
     |
     v
recheck hash + repo + HEAD + git apply --check
     |
     v
apply stored diff only
```

If the stored diff changes by one byte, or Git `HEAD` moves after staging, the patch cannot be approved/applied and must be restaged.

An old v0.3 proposal in `approved` state is migrated only to `plan_approved`; it does **not** inherit permission to apply a patch.

## Data boundary

Kept local on the Mac:

- registered source repositories;
- Chroma vector database;
- SQLite FTS5 index;
- deterministic knowledge graph;
- repository catalogue/fingerprints;
- conversation Markdown;
- `PROJECT.md` project memory;
- generated files, proposals, staged patches and context debug snapshots;
- local project registry;
- local API authentication token.

Sent through the configured enterprise Portkey route only after the egress checks above:

- approved-safe source/document chunks when embeddings are generated;
- embedding search queries;
- selected compiled context and user request for GPT inference.

## Retrieval architecture

```text
                         question
                            |
       +--------------------+--------------------+
       |                    |                    |
       v                    v                    v
 exact identifiers      SQLite FTS5       Portkey embeddings
 / paths / symbols       lexical RAG            Chroma
       |                    |                    |
       +--------------------+--------------------+
                            |
                      repo routing
                            |
                   knowledge graph search
                  + structural expansion
                            |
                     fused candidates
                            |
                      context compiler
          + PROJECT.md + recent conversation
                            |
                         GPT-5.6
```

Code chunks retain repo/path/language/symbol/line metadata. Git-backed repos use `git ls-files` and attach branch/commit metadata.

## Requirements

- Python 3.11+
- Node.js 20+
- npm
- Git
- an enterprise-approved Portkey route exposing GPT-5.6 and an embedding model

## Install

Backend:

```bash
cd project-assistant-v0.4
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Configure the approved Portkey route:

```bash
export PORTKEY_BASE_URL='https://your-approved-portkey-endpoint/v1'
export PORTKEY_API_KEY='...'
export PORTKEY_CHAT_MODEL='gpt-5.6'
export PORTKEY_EMBEDDING_MODEL='your-approved-embedding-model'
```

Optional enterprise routing:

```bash
export PORTKEY_CHAT_VIRTUAL_KEY='...'
export PORTKEY_EMBEDDING_VIRTUAL_KEY='...'
export PORTKEY_CHAT_CONFIG_ID='...'
export PORTKEY_EMBEDDING_CONFIG_ID='...'
export PORTKEY_EXTRA_HEADERS_JSON='{}'
```

Frontend:

```bash
cd web
npm install
```

Do **not** put Portkey credentials in `web/.env.local`. The frontend server only needs access to the local FastAPI service and local assistant token.

## Run

Easiest after dependencies are installed:

```bash
./scripts/dev.sh
```

Or separately:

```bash
# terminal 1
source .venv/bin/activate
project-assistant-api

# terminal 2
cd web
npm run dev
```

Open:

```text
http://127.0.0.1:3000
```

FastAPI binds to `127.0.0.1:8000` by default.

## First use

Register an existing project-assistant directory, or create a new project from the left sidebar. Nothing is copied; the registry stores project paths only.

Add source repositories under **Project**, then choose **Reindex changed files**.

The indexer:

1. discovers tracked files with `git ls-files` for Git repos;
2. rejects sensitive paths / escaping symlinks;
3. fingerprints new/changed/deleted content;
4. builds code-aware chunks;
5. updates local FTS and knowledge-graph state;
6. secret-scans file content;
7. sends only safe chunks to the configured Portkey embedding model;
8. updates Chroma.

The indexing result includes a `local_only` count for changed files whose remote embedding was blocked.

## Change workflow in the web UI

Switch the composer to **Propose change**. The assistant creates a plan only and writes it to the conversation ledger.

After **Approve plan**, you may stage a local unified diff against a registered Git repo. The backend validates it and records the exact diff, SHA-256 and base commit in the conversation.

Only after **Approve exact diff** does the Apply button become available. Apply takes no arbitrary patch/repo parameters; it can apply only the previously staged and approved immutable diff.

## Context inspector

The right panel shows:

- estimated token count;
- routed repositories;
- retrieved repo/path/line/symbol labels;
- graph sources using repo-relative paths;
- the exact compiled context when manually inspected.

The latest snapshot is stored locally at:

```text
.assistant/debug/last_context.md
```

## CLI

```bash
project-assistant --project /path/to/project index
project-assistant --project /path/to/project search 'where is IKJEFT01 invoked?'
project-assistant --project /path/to/project graph IKJEFT01
project-assistant --project /path/to/project context 'where do we execute TSO commands from batch?'

project-assistant --project /path/to/project propose <conversation-id> 'change request'
project-assistant --project /path/to/project approve <proposal-id>
project-assistant --project /path/to/project stage-patch <proposal-id> /path/change.diff --repo /path/repo
project-assistant --project /path/to/project approve-patch <proposal-id>
project-assistant --project /path/to/project apply-patch <proposal-id>
```

## Local project layout

```text
project/
├── PROJECT.md
├── assistant_system.md
└── .assistant/
    ├── project.json
    ├── conversations/
    ├── generated/
    ├── proposals/
    ├── patches/
    ├── chroma/
    ├── lexical.sqlite3
    ├── knowledge_graph.sqlite3
    ├── index_manifest.json
    ├── security_events.log
    └── debug/last_context.md
```

`.assistant/` is added to the repo's local `.git/info/exclude` when a project is initialised, so local assistant state is not accidentally committed and no shared `.gitignore` change is required.

## Testing

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

v0.4 currently has 17 backend/core tests covering the two-stage approval gate, patch tampering, HEAD changes, secret detection, path traversal, API authentication, retrieval and graph behaviour.

After `npm install`:

```bash
cd web
npm run typecheck
npm run build
```

## Deliberate limitations

- Secret detection is a defensive filter, not a substitute for the organisation's DLP/egress controls.
- Patch generation is still not automated; v0.4 stages an existing local unified diff after plan approval.
- Git indexing excludes brand-new untracked files.
- No filesystem watcher or reranker yet.
- The knowledge graph is deterministic/structural rather than LLM-generated.
- This remains a single-user local tool rather than a network service.
