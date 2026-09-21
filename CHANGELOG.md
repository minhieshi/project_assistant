# Changelog

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
