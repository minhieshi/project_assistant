# v0.7.8

## 0.7.8 — Authoritative live Git change context

- Change-mode retrieval now receives live repository state for **all** registered source roots, independent of RAG hits or repo routing.
- Git-backed sources expose current branch, HEAD and clean/dirty working-tree state; non-Git sources are explicitly marked Git-not-applicable.
- Before declaring a change plan sufficient, the retrieval planner is instructed to read likely target files live and use `git_show(HEAD, path)` when the committed version matters.
- Proposal generation no longer treats diff staging/hash generation as a model responsibility; the backend performs staging, SHA-256 binding and HEAD verification only after plan approval.
- The project repo is now included in the registered writable roots alongside external source repos.
