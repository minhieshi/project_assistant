# Local Project Assistant — v0.8.0

Project Assistant recreates the useful parts of the enterprise ChatGPT browser experience locally while using approved Portkey routes for GPT-5.6 inference and embeddings. It keeps persistent project conversations, indexes multiple repositories, builds high-signal project context, and can now produce source-grounded **copy-pasteable implementation code** without writing to the repositories itself.

Its boundary is deliberate:

```text
Project Assistant
  understand / retrieve / investigate / design
  break large changes into small implementation steps
  author complete code/config/tests for the current step
                    |
                    v
                  Human
  review / copy-paste / run / test / commit
```

Registered source repositories remain read-only to Project Assistant. It does not apply patches, stage files, commit, or expose arbitrary shell execution.

## v0.8.0 — Guided implementation

The composer has two modes:

- **Chat** — project questions, debugging, architecture, design and investigation.
- **Guided implementation** — implementation work with a human approval boundary.

For a non-trivial coding request, Guided implementation:

1. uses conversation-aware RAG plus bounded live read-only exploration;
2. breaks the work into 2–6 small coherent steps;
3. explains the approach and stops before implementation code;
4. waits for the user to confirm the next step;
5. re-reads the relevant live source for that turn;
6. emits complete copy-pasteable code for **one step only**;
7. provides validation commands/checks and stops again for user input.

The conversation itself is the workflow state. There is no separate orchestration engine and no autonomous source mutation.

### Copy-paste code contract

For each changed artefact, GPT is instructed to provide:

```text
Repository: <registered repository>
File: <relative/path>
Action: Create file | Replace file | Replace function/class/section | Insert at exact anchor
Why: <short explanation>
```

Then it must provide a complete pasteable unit:

- new files: complete file;
- small/medium existing files: prefer complete replacement file;
- large files: complete replacement function/class/contiguous section with an exact anchor;
- atomic multi-file steps: include every file needed for that step;
- no `...`, `existing code`, `rest unchanged`, omitted imports or pseudo-code inside replacement blocks;
- no diff unless explicitly requested.

Assistant responses have a **Copy response** control and fenced code blocks have their own **Copy** control.

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

Normal Chat and Guided implementation automatically perform conversation-aware retrieval. **Compile context** remains an inspection/debugging feature and is not required before asking a question.

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

Guided implementation has a stronger retrieval contract: likely target files must be located and read live where possible before the model declares context sufficient for copy-paste code generation. This is particularly important for short continuation turns such as `yes`, `next`, or `do step 2`, where the retrieval planner uses recent conversation state to recover the target files.

Live file/Git tools operate only inside the Project Assistant workspace and explicitly registered source roots. They do not expose an arbitrary shell and do not write to source repositories.

## Projects and source roots

A Project Assistant project owns its conversations/settings/indexes. Source repositories can live anywhere allowed by the source-root security policy and remain independent repositories/directories.

Recommended layout:

```text
~/.project-assistant/projects/mainframe-platform/   # Project Assistant workspace
~/work/mainframe/ansible/                           # registered source
~/work/mainframe/java/                              # registered source
~/work/mainframe/config/                            # registered source, Git optional
```

Register each independent child source rather than making a parent folder into a nested Git repository. Both Git-backed and plain source directories are supported.

## Indexing

**Reindex changed files** is incremental:

- unchanged file content is not re-embedded;
- branch/HEAD metadata can refresh independently of content fingerprints;
- archives/compiled/binary/generated/oversized/sensitive artefacts are skipped;
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

- FastAPI binds to loopback by default.
- Every `/api/*` request requires a generated local API token.
- Next.js adds that token server-side; browser JavaScript does not receive it.
- Portkey credentials remain backend-only.
- source roots must pass registration validation;
- symlink escapes outside registered roots are rejected;
- sensitive credential paths/files are excluded;
- high-confidence secret material is blocked from outbound embedding/chat content;
- local-only retrieval can remain available when egress is not permitted;
- retrieved source is treated as untrusted evidence.

Project Assistant may **author** code but may not **apply** it. Historical `change_gate.py` compatibility code/data remains present but is not wired into the active v0.8.0 API/UI workflow.

## Persistent local state

Project-local assistant state lives under `.assistant/`, including the semantic/lexical/graph indexes, index metadata/status, context debug snapshots and conversation Markdown. Managed/imported project discovery state lives under `~/.project-assistant/`.

## Frontend

- composer text is isolated from page-level React state;
- historical Markdown messages are memoised;
- streamed output is buffered rather than re-rendering per tiny token fragment;
- compiled context is collapsed by default;
- off-screen historical messages use browser content visibility;
- assistant responses and fenced code blocks have copy controls.

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

Configure the required Portkey environment variables, then start the backend and frontend using your existing workflow or `scripts/dev.sh`.

No reindex is required solely for the v0.8.0 guided-implementation change. Reindex only when source/index metadata itself needs refreshing.

## CLI

```bash
project-assistant projects
project-assistant --project /path/to/project index
project-assistant --project /path/to/project chat-new "Investigation"
project-assistant --project /path/to/project chat <conversation-id> "Why is this failing?"
project-assistant --project /path/to/project chat <conversation-id> "Implement the next step" --guided
project-assistant --project /path/to/project context "How does asset rebuild flow across repos?"
project-assistant --project /path/to/project search "IKJEFT01"
project-assistant --project /path/to/project graph "AssetLookup"
```
