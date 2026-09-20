# Changelog

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
