## 0.7.2 — macOS Dictation simplification

- Removed the `whisper.cpp` backend, microphone recording endpoint, WAV encoder, model-path environment variables, and Whisper status plumbing.
- Project Assistant now relies on native macOS Dictation directly in the chat textarea.
- No speech audio is captured, stored, or transmitted by Project Assistant.
- Added a small UI hint pointing users to their configured macOS Dictation shortcut.

## 0.7.1 — Retrieval fail-soft hotfix

- Semantic embedding failures during chat/context compilation no longer abort the request.
- Retrieval falls back to local FTS, exact matching and the knowledge graph when Portkey/Bedrock embeddings return 5xx errors.
- Semantic query text is bounded before remote embedding so pasted logs/error dumps do not create oversized query requests.
- Hybrid `search_project` retrieval also falls back to lexical/exact search if vector search is unavailable.
- Portkey HTML gateway error pages are sanitised to short plain-text diagnostics instead of surfacing raw HTML in the web UI.
- Context inspector exposes retrieval warnings; warnings are diagnostic only and are not injected into the model context.

# Changelog

## v0.7.0 — Retrieval 2.0

- Added conversation-aware multi-query retrieval; previous user turns now influence source search rather than only appearing in the final prompt.
- Repository routing is now a ranking boost, not a hard filter.
- Increased final project context from a narrow 12 chunks to roughly 30 diversified/coherent chunks within the configured token budget.
- Added coherent expansion around adjacent chunks, repeated-hit files and knowledge-graph neighbours.
- Added a provider-safe GPT retrieval-planning pass with read-only local operations: `search_project`, `search_exact`, `find_symbol`, `find_references`, `read_file`, and `read_file_range`.
- Bounded planner semantic searches to avoid turning one chat into a large burst of embedding requests.
- Added Java knowledge-graph extraction for classes, methods, imports and approximate calls.
- Added independent graph index versioning so existing vectors are reused while structural graph data can refresh locally.
- Context inspector/SSE now exposes retrieval query variants and agent retrieval actions.
- Default context budget increased to 48K tokens (`CONTEXT_MAX_TOKENS` remains configurable).
- Added explicit prompt guidance not to ask users to paste indexed files until project retrieval has been attempted.

## v0.6.7.5 — Markdown rendering + local dictation

- Assistant/system/event messages now render GitHub-flavoured Markdown in the local web UI.
- Fenced shell/code blocks, inline code, headings, lists, tables, blockquotes and links are styled for readability.
- Markdown rendering uses `react-markdown` without raw-HTML rendering.
- Added optional local-only dictation: browser microphone audio is encoded as PCM WAV, proxied only to the loopback FastAPI service, transcribed by a locally installed `whisper.cpp` `whisper-cli`, then inserted into the composer for review before send.
- Dictation requires an explicit local model path and performs no model download or external speech API call.
- Added dictation status to `/api/status` and regression tests for local WAV/transcript handling.

## v0.6.7.4 — Balanced egress policy

- Relaxed source/chat text scanning to block only high-confidence credential material.
- Credential-looking references, vault paths, environment-variable references and config identifiers are advisory and no longer prevent embedding/chat.
- Sensitive file/path exclusions remain in place (`.env`, private-key/certificate files, credential directories).
- Hard-blocked local-only chunks are tagged `egress_allowed=false` and excluded from compiled outbound GPT context.
- Added regression tests for reference-heavy source code and context filtering.

## v0.6.7.3

- Fixed Project-tab vertical scrolling when many source repositories/index rows make the page taller than the viewport.
- Added the missing `min-height: 0` constraints to the grid/flex containers so the bounded project panel can actually scroll.
- Sidebar and inspector now use independent vertical scrolling with horizontal overflow suppressed.
- Added stable scrollbar space and a small bottom spacer so the Add source repo controls remain comfortable to reach.
- No indexing, RAG, Portkey, or persistence behaviour changes.

## v0.6.7.2

- Fixed `NameError: SKIP_DIRS is not defined` in the non-Git filesystem walker introduced by v0.6.7.
- Centralised skipped-directory policy in `index_policy.py`.
- Added regression coverage for non-Git source walking so build/cache directories remain excluded.
- Clean-install validation now imports the package and starts the FastAPI app before release packaging.

# 0.6.7.1

- Fix packaging/install issue that could leave `project_assistant.index_status` unavailable at runtime.
- Flatten release directory so the package root is unambiguous.
- Switch build backend from Hatchling to setuptools and add a compatibility `setup.py`.
- No indexing/RAG behaviour changes from 0.6.7.

# Changelog

## v0.6.7

- Added pre-index file classification with explicit skip reasons for archives, compiled/binary artefacts, unsupported types, sensitive paths, oversized files, large generated source and minified/extreme-long-line content.
- Added hard final chunk bounds so a single enormous source line can no longer bypass the chunk-size limit; PDF windows are hard-bounded as well.
- A provider rejection for one eligible file no longer aborts the whole repository: the file remains available through local lexical/graph retrieval and is marked `embedding-rejected` / local-only.
- Added persisted `.assistant/index_status.json` with live/current file, totals, per-repo counts, skip/local-only reasons and recent skipped files.
- Added `/api/projects/{id}/index-status` and a web **Index visibility** panel that polls while indexing is running.
- Index manifests are saved after each changed/new file so completed work is resumable if a later file fails.
- Added regression coverage for hard single-line bounds, archive/compiled/generated exclusions and index-status persistence; backend/core suite is now 32 passing tests.

## v0.6.6

- Replaced the embedding SDK path with raw HTTP that exactly mirrors the confirmed working curl request.
- Embeddings now POST to `$PORTKEY_BASE_URL/embeddings` with only `x-portkey-api-key` and `Content-Type: application/json`.
- Embedding JSON now contains exactly `model` and `input`; the full enterprise model slug is passed unchanged.
- Embedding virtual-key/config/extra-header settings are deliberately ignored on this path so the request remains identical to the working curl.
- Added regression tests that inspect the actual URL, HTTP method, headers, and serialized JSON request body.

## v0.6.5

- Corrected the Portkey embedding resource: embeddings now call `Portkey(...).embeddings.create(model=<full configured model>, input=<raw text>)`.
- The full enterprise model ID (for example `@bedrock-au/amazon.titan-embed-text-v2:0`) is passed unchanged; Project Assistant does not split the provider slug.
- Removed the mistaken `completion.create(...)` embedding path, which can leave the provider-facing embedding text null because completions use a different request schema.
- No `provider`, `encoding_format`, `input_type`, dimensions, normalisation, or Bedrock-native fields are added by Project Assistant.
- The real Portkey API key and enterprise base URL remain environment-backed.
- Regression tests assert the exact minimal SDK call: only `model` and `input`.

## v0.6.3

- Switched embeddings to Portkey's official Python SDK (`from portkey_ai import Portkey`).
- Provider-prefixed embedding routes are split so `@bedrock-au/amazon.titan-embed-text-v2:0` becomes SDK provider `@bedrock-au` plus model `amazon.titan-embed-text-v2:0`.
- Titan embeddings now call `portkey.embeddings.create(model=..., input=...)` with one raw string and no application-added Bedrock inference fields.
- Switched chat to the Portkey Python SDK as well, removing the dummy OpenAI SDK API-key placeholder from the inference path.
- Preserved separate chat/embedding virtual-key and config-ID support plus explicit enterprise `PORTKEY_BASE_URL`.
- Added Portkey SDK/provider-splitting regression coverage, including Cohere query/document input types.
- `PORTKEY_REASONING_EFFORT` remains defaulted/capped to `high`; `chat-test` still checks streaming and non-streaming routes.
- Backend/core suite: 30 passing tests.

## v0.6.1

- Added a Portkey/Amazon Titan Text Embeddings V2 adapter for `@bedrock-au/amazon.titan-embed-text-v2:0`.
- Titan embedding requests now send exactly one raw string per Portkey `/embeddings` call rather than using LangChain batching/pre-tokenisation.
- Omit optional Titan fields by default, allowing Bedrock defaults of 1024 dimensions and normalisation.
- Generic non-Titan `OpenAIEmbeddings` now sets `check_embedding_ctx_length=False` so raw strings are preserved for non-OpenAI providers.
- Added `project-assistant embedding-test` to verify the configured route before indexing real source code.
- Added regression tests asserting the exact Titan request shape, including Portkey provider-prefixed model names.
- Backend/core suite: 25 passing tests.

## v0.6.0

- Added project rename support without changing repository identity or path.
- Added safe **Convert imported project to source** workflow for correcting a repo imported as a project by mistake.
- Conversion adds the existing repo to a selected target project's source list, then removes only the imported-project catalogue entry. No repository files are moved or deleted.
- Added **Forget as project** for imported repos and source-removal controls in the web UI.
- Clarified create/import UI wording so reference code repos are directed to Project → Sources.
- Added CLI commands `project-rename`, `project-convert-to-source` and `project-forget`.
- Added regression coverage for restart persistence, failed conversion safety, conversion without repo movement, and rename identity preservation.
- Backend/core suite: 23 passing tests.

## v0.5.0

- Replaced path-first project creation with a managed-project model: creating a project now creates a local Git repo under `~/.project-assistant/projects` (configurable).
- Added explicit existing-Git-repo import without copying or moving source code. Imported assistant metadata stays under `.assistant/`.
- Managed projects are rediscovered from disk on every backend start; imported repos are restored from `~/.project-assistant/imports.json`.
- Added automatic migration of valid v0.4 `registry.json` entries so existing projects reappear after upgrade.
- Project listing/status no longer initialises Chroma or requires Portkey configuration.
- Removed the public Portkey URL fallback: remote embeddings/inference fail closed until `PORTKEY_BASE_URL` is explicitly configured.
- Added `/api/status` so the UI can show the projects root and Portkey setup state.
- Split the web UI into **Create project repo** and **Import Git repo** flows and show existing projects immediately on startup.
- Fixed proxy error semantics: origin violations are 403, missing local backend token is 503, and unreachable FastAPI is 502 instead of all failures appearing as 403.
- Added lazy CLI project management (`projects`, `project-create`, `project-import`) that works without loading RAG dependencies.
- Added restart/import/v0.4-migration/fail-closed Portkey regression coverage; backend/core suite is now 20 tests.

## v0.4.0

- Added authenticated local API access using a user-only token; browser JavaScript no longer calls FastAPI directly.
- Added a Next.js server-side proxy that injects the local token, rejects cross-site state-changing requests and only proxies to loopback.
- FastAPI now refuses non-loopback binding by default.
- `dev.sh` removes Portkey credentials from the frontend process environment.
- Added safe project-path resolution and rejection of project config paths that escape the project root.
- Added source-root restrictions for filesystem/home roots and common credential directories.
- Added sensitive filename exclusions, escaping-symlink protection and pre-egress credential scanning.
- Files containing credential-like content are kept local-only instead of being embedded remotely.
- Added final pre-Portkey scans for embedding queries and compiled GPT prompts.
- Removed absolute graph paths from compiled GPT context and marked retrieved source as untrusted evidence.
- Replaced single approval with plan approval + exact-diff approval.
- Staged patches are copied locally, hashed, bound to a registered repo and Git HEAD, written to the conversation ledger and revalidated before apply.
- Added backwards-safe migration of old `approved` proposals to plan approval only.
- Added 17 regression/security tests including API auth, path traversal, patch tampering and HEAD-change invalidation.

## v0.3.0

- Added a local Next.js/TypeScript web interface while retaining the CLI.
- Added a FastAPI API layer bound to `127.0.0.1` by default.
- Added a local project registry that stores filesystem paths only.
- Added project creation/registration and local source-repository management.
- Added conversation listing/rendering from the existing Markdown ledger.
- Added SSE chat streaming through GPT/Portkey; the browser never receives Portkey credentials.
- Added a visible Chat / Propose change mode split.
- Added proposal approval/rejection cards backed by the existing server-side change gate.
- Added guarded patch application from the UI for already-generated local unified diffs; approval and `git apply --check` are still mandatory.
- Added a context-inspector panel exposing routed repos, retrieved chunks, graph sources, compiled context and token estimate.
- Decoupled approval/rejection/patch application from Portkey/Chroma availability.
- Added web-foundation tests for conversation parsing, project registry behaviour and lazy API startup.

## v0.2.0

- Added Git-aware source discovery via `git ls-files`.
- Added code-aware/structural chunking and line/symbol metadata.
- Added SQLite FTS5 lexical and exact identifier retrieval beside Chroma semantic retrieval.
- Added repository catalogue/routing and knowledge-graph retrieval expansion.
- Added Git branch/commit metadata to chunks and context source labels.

## v0.1.0

- Added Portkey-backed embeddings/chat, incremental Chroma indexing, Markdown conversations, deterministic knowledge graph, bounded context compiler and explicit approval-gated patch application.
