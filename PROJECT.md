# Local Project Assistant

## Purpose

Restore the high-value parts of the enterprise ChatGPT browser experience locally while using approved Portkey routes for GPT-5.6 inference and embeddings.

## Architecture

- Git-backed local project workspaces with multiple registered source repositories.
- Next.js browser UI with server-side proxy to an authenticated loopback-only FastAPI API.
- Durable Markdown conversation history.
- Incremental Chroma semantic index using approved Portkey embeddings.
- Local SQLite FTS5 lexical/exact index.
- Deterministic code-aware knowledge graph.
- Repository catalogue/router and bounded context compiler.
- GPT-5.6 inference through Portkey.
- Pre-egress path/secret controls.
- Two-stage plan + exact-diff approval before source mutation.

## Decisions

- Treat source code as first-class RAG material.
- Preserve repo/branch/commit/path/symbol/line metadata on code chunks.
- Prefer structural chunks to fixed character chunks when language structure is detectable.
- Use hybrid retrieval: exact + lexical + semantic + graph expansion.
- Route across repos before spending context budget, but never suppress strong global exact matches.
- Keep graph extraction deterministic rather than LLM-generated.
- Conversation Markdown is the first durable AI output for any proposed code change.
- Browser JavaScript receives neither Portkey credentials nor the local FastAPI token.
- A plan approval authorises preparation of a diff only; source mutation requires approval of the exact hashed diff.
- Bind a staged diff to its registered repo and Git HEAD so approval is not reusable after source state changes.
- Treat obvious secrets and sensitive credential files as non-egressable; local-only retrieval is allowed where appropriate.
- Keep assistant state out of Git using local `.git/info/exclude` rather than modifying shared repository rules.
- Create new projects as managed local Git repos; import existing repos in place without copying them.
- Discover managed projects from disk at startup and persist only external imported-repo pointers.
- Require an explicit enterprise `PORTKEY_BASE_URL`; never silently fall back to a public gateway.

## Current work

v0.6.7 makes multi-repo indexing safer and observable: unsuitable artefacts are excluded before embedding, final chunks are hard-bounded, provider-rejected files remain local-only, and the UI shows persisted per-repo indexing progress and skip reasons.

## Known issues

- Titan direct-HTTP embeddings and GPT chat still require live verification against the enterprise Portkey gateway; local request-shape tests now cover both paths.
- Secret scanning is intentionally conservative and should complement, not replace, enterprise DLP controls.
- Git indexing excludes brand-new untracked files.
- No filesystem watcher or reranker yet.


## v0.6 project correction

- Projects can be renamed without moving their repository or changing project identity.
- Imported projects can be converted into source repos of another project without copying, moving or deleting the repo.
- Imported projects can be forgotten safely; managed projects remain disk-discovered and cannot be removed via the catalogue.
- Source repositories can be removed from a project in the web UI.
- Import UI now distinguishes a project repo from a reference/source repo.


## v0.6.1 Titan embedding compatibility

The configured enterprise embedding route is `@bedrock-au/amazon.titan-embed-text-v2:0`. Titan V2 uses a dedicated Portkey adapter that sends one raw string per embedding request and leaves optional dimensions/normalisation fields unset so Bedrock defaults apply. A fixed-string `project-assistant embedding-test` command is available before indexing real source code.


## v0.6.3 Portkey SDK alignment

- Embeddings use Portkey's official Python SDK instead of hand-built HTTP or the OpenAI SDK.
- The enterprise embedding model slug is treated as opaque and passed unchanged to Portkey.
- Embedding calls send only `model=<full model slug>` and one raw string `input`.
- Chat also uses the Portkey Python SDK directly; the long enterprise model slug is passed through unchanged.
- Real Portkey credentials remain environment-backed and are supplied to the SDK at runtime.
- Chat reasoning defaults to the enterprise-supported `high` ceiling for both Chat Completions and Responses modes.
- `embedding-test` and `chat-test` provide small, non-sensitive route validation before indexing or normal conversation use.

## v0.6.5 embedding simplification

- Embeddings use `Portkey(...).embeddings.create(model=<full model id>, input=<raw text>)`.
- The previous `completion.create(...)` attempt was removed because completions and embeddings use different request schemas.
- No model/provider splitting or embedding-specific request fields are applied.


## v0.6.6 embedding transport

Embedding transport is intentionally a literal Python equivalent of the confirmed working curl: POST `{PORTKEY_BASE_URL}/embeddings`, send `x-portkey-api-key` and JSON content type, and serialize only `model` plus raw `input`. No embedding SDK or provider-specific translation is permitted on this path.


## v0.6.7 safe indexing and visibility

- Automatically exclude archive/compiled/binary/unsupported/sensitive/oversized/generated/minified artefacts before Portkey embedding.
- Hard-bound every final source/PDF chunk so pathological long lines cannot exceed the configured embedding chunk size.
- Preserve local lexical/graph access when a particular file cannot be embedded remotely.
- Persist index progress and per-repo statistics to `.assistant/index_status.json` and surface them in the web UI.


## v0.7 retrieval decision

Retrieval is now iterative and conversation-aware. GPT may plan additional read-only searches over indexed sources before producing a final response. Repo routing is a boost only. Existing vector embeddings are retained; graph parser upgrades refresh locally via `graph_index_version`.


## v0.7.1 hotfix
Semantic/vector retrieval and the retrieval-planner pass are fail-soft. Portkey/Bedrock embedding failures no longer abort chat; local FTS/exact/knowledge-graph retrieval continues, semantic queries are bounded, and gateway HTML errors are sanitised.
