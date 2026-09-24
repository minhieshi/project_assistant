# v0.8.0

## 0.8.0 — Guided implementation and copy-paste code

- Replaces the OpenCode-first handoff workflow with **Guided implementation** while keeping all registered source roots read-only.
- Large implementation requests are decomposed into small steps; the assistant explains the plan and pauses before coding.
- Confirmed steps use a dedicated implementation retrieval mode that re-opens likely target files live before code generation.
- GPT may author complete source/config/test code for the human to copy into the repository, but it still cannot edit, stage, commit, build or test source itself.
- Adds a strict copy-paste contract: exact repository/path/action metadata, complete new files or replacement units, no ellipsis/placeholders, and no diffs unless requested.
- Removes the public implementation-brief endpoint and OpenCode mode from the web UI.
- Adds response-level and fenced-code copy buttons.
- Adds `--guided` to the CLI chat command.

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
