# Local Project Assistant

## Purpose

Restore the high-value parts of the enterprise ChatGPT browser experience locally while using approved Portkey routes for GPT-5.6 inference and embeddings.

## Architecture

- Local project workspace with multiple registered source repositories.
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

## Current work

v0.4 implements the first security-hardening pass: authenticated local API isolation, filesystem/egress controls and exact-diff approval binding.

## Known issues

- Portkey embedding/Chroma path still requires live verification against the enterprise gateway and chosen embedding model.
- Secret scanning is intentionally conservative and should complement, not replace, enterprise DLP controls.
- Git indexing excludes brand-new untracked files.
- No filesystem watcher or reranker yet.
