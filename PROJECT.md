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

v0.6.2 hardens the Portkey integration: Titan embeddings now use an exact direct-HTTP schema, and chat requests explicitly use the enterprise-supported `high` reasoning level with a connectivity/streaming test command.

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


## v0.6.2 Portkey request hardening

- Titan V2 bypasses LangChain and the OpenAI SDK for embeddings so the outbound JSON is fully controlled.
- Exact Titan payload: provider-prefixed model, one raw string input, float encoding; dimensions/normalisation use Bedrock defaults.
- Real Portkey credentials remain environment-backed headers; SDK placeholder credentials are never enterprise secrets.
- Chat reasoning defaults to the enterprise-supported `high` ceiling for both Chat Completions and Responses modes.
- `embedding-test` and `chat-test` provide small, non-sensitive route validation before indexing or normal conversation use.
