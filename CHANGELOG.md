# v0.8.1

## 0.8.1 — MCP credential foundation on v0.8.0 memory

- Builds on v0.8.0 without changing the consolidated conversation memory layer.
- Adds a reusable MCP credential-store abstraction for upcoming remote MCP connectivity.
- Uses the native macOS Keychain by default on macOS; OAuth bundles are stored as generic-password items and never written to project configuration.
- Stores access token, refresh token, expiry and client-registration metadata together as opaque JSON for OAuth refresh/re-authentication flows.
- Adds an explicit private-file fallback (`PROJECT_ASSISTANT_MCP_AUTH_STORE=file`) for development/non-macOS use; the file is restricted to mode 0600.
- Keeps credential lookup failures separate from project/retrieval state so future remote MCP connections can surface `authentication required` cleanly.

# v0.8.0

## 0.8.0 — Consolidated conversation memory

- Added incremental 24-hour conversation consolidation using only entries since the previous successful consolidation.
- Added generated daily consolidation Markdown and derived user-memory Markdown.
- Context compilation now prefers user memory + consolidated history and filters raw conversation/generated memory out of normal project-code RAG.
- Raw historical chat remains indexed only as a fallback for exact-history recall or when no consolidation exists.
- Added manual CLI/API consolidation and memory status endpoints.
- Added an in-process hourly due check; actual model consolidation is limited to once per configured 24-hour interval unless manually forced.
- Added tests for incremental consolidation, 24-hour due logic and memory-aware context retrieval.

# v0.7.9

## 0.7.9 — Read-only project intelligence + OpenCode handoff

- Pivots Project Assistant away from source mutation and into read-only project intelligence.
- Removes proposal approval, patch staging, exact-diff approval and apply routes from the public API/UI.
- Adds **OpenCode brief** mode in the composer. It can use the current conversation as the focus or accept an optional handoff focus.
- Adds multi-round handoff retrieval that locates concrete repositories, relative paths, symbols, integration boundaries, configuration and tests, with live reads where possible.
- Generates a structured, source-grounded implementation brief and stores it in the conversation Markdown.
- Adds **Copy for OpenCode** directly on generated implementation briefs.
- Keeps project/source registration, indexing, RAG, graph retrieval, live read-only filesystem/Git inspection, context inspection and chat unchanged.
- Source repositories are not modified by Project Assistant; OpenCode remains responsible for edits, shell commands, builds, tests and Git changes.

# v0.7.8

## 0.7.8 — Authoritative live Git change context

- Change-mode retrieval now receives live repository state for **all** registered source roots, independent of RAG hits or repo routing.
- Git-backed sources expose current branch, HEAD and clean/dirty working-tree state; non-Git sources are explicitly marked Git-not-applicable.
- Before declaring a change plan sufficient, the retrieval planner is instructed to read likely target files live and use `git_show(HEAD, path)` when the committed version matters.
- Proposal generation no longer treats diff staging/hash generation as a model responsibility; the backend performs staging, SHA-256 binding and HEAD verification only after plan approval.
- The project repo is now included in the registered writable roots alongside external source repos.
