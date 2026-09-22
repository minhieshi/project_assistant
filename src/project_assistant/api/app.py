from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import ProjectConfig, SourceRoot
from ..conversations import ConversationStore
from ..knowledge_graph import KnowledgeGraph
from ..index_status import IndexStatusStore
from ..security import load_or_create_api_token, tokens_equal, validate_source_root
from .dependencies import assistant_for, invalidate, project_path, registry
from .schemas import (
    ChatRequest,
    ConversationCreateRequest,
    ProjectCreateRequest,
    ProjectImportRequest,
    ProjectUpdateRequest,
    ProjectConvertToSourceRequest,
    ProjectPathRequest,
    ImplementationBriefRequest,
    QueryRequest,
    SourceRequest,
)


API_TOKEN = load_or_create_api_token()
app = FastAPI(title="Local Project Assistant", version="0.8.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_api_auth(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-project-assistant-token", "")
        if not supplied or not tokens_equal(supplied, API_TOKEN):
            return JSONResponse({"detail": "Unauthorised local API request"}, status_code=401)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _safe_error_message(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if "<html" in message.lower() or "<!doctype" in message.lower() or re.search(r"</?[a-z][^>]*>", message, re.I):
        message = re.sub(r"<script.*?</script>|<style.*?</style>", " ", message, flags=re.I | re.S)
        message = re.sub(r"<[^>]+>", " ", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[:700]


def _error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=_safe_error_message(exc))


def _project_info(project_id: str) -> dict:
    registered = registry.get(project_id)
    path = Path(registered.path)
    config = ProjectConfig.load(path)
    return {
        "id": registered.id,
        "name": config.name,
        "path": str(path),
        "kind": registered.kind,
        "sources": [
            {"name": source.name, "path": str(Path(source.path).expanduser().resolve())}
            for source in config.resolved_sources(path)
        ],
        "zowe_systems": [
            {"name": system.name, "enabled": system.enabled, "allowed_tools": list(system.allowed_tools)}
            for system in config.zowe_systems
        ],
    }


def _store(project_id: str) -> ConversationStore:
    path = project_path(project_id)
    config = ProjectConfig.load(path)
    return ConversationStore(config.project_path(path, config.conversation_dir))


def _sse(event: str, payload: dict | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.8.0"}


@app.get("/api/status")
def status() -> dict:
    # Project discovery must remain usable even when Portkey is not yet configured.
    from ..config import PortkeySettings

    settings = PortkeySettings.from_env()
    return {
        "version": "0.8.0",
        "projects_root": str(registry.projects_root),
        "portkey": {
            "base_url": settings.base_url,
            "base_url_configured": bool(settings.base_url),
            "api_key_configured": bool(settings.api_key),
            "chat_model": settings.chat_model,
            "api_mode": settings.api_mode,
            "reasoning_effort": settings.reasoning_effort,
            "embedding_model_configured": bool(settings.embedding_model),
        },
    }



@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [_project_info(project.id) for project in registry.list()]


@app.post("/api/projects")
def create_project(body: ProjectCreateRequest) -> dict:
    try:
        project = registry.create(body.name)
        return _project_info(project.id)
    except Exception as exc:
        raise _error(exc)


@app.post("/api/projects/import")
def import_project(body: ProjectImportRequest) -> dict:
    try:
        project = registry.import_repo(Path(body.path), body.name)
        return _project_info(project.id)
    except Exception as exc:
        raise _error(exc)


# Compatibility endpoint for v0.4 clients. Registration now means importing
# an existing Git repository.
@app.post("/api/projects/register")
def register_project(body: ProjectPathRequest) -> dict:
    try:
        project = registry.import_repo(Path(body.path))
        return _project_info(project.id)
    except Exception as exc:
        raise _error(exc)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    try:
        return _project_info(project_id)
    except Exception as exc:
        raise _error(exc, 404)


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest) -> dict:
    try:
        project = registry.rename(project_id, body.name)
        invalidate(project_id)
        return _project_info(project.id)
    except Exception as exc:
        raise _error(exc)


@app.post("/api/projects/{project_id}/convert-to-source")
def convert_project_to_source(project_id: str, body: ProjectConvertToSourceRequest) -> dict:
    try:
        # Invalidate while the imported project is still discoverable. After a
        # successful conversion it is intentionally removed from the catalogue.
        invalidate(project_id)
        target, source = registry.convert_imported_to_source(
            project_id, body.target_project_id, body.source_name
        )
        invalidate(target.id)
        return {
            "removed_project_id": project_id,
            "target_project": _project_info(target.id),
            "source": {"name": source.name, "path": source.path},
        }
    except Exception as exc:
        raise _error(exc)


@app.delete("/api/projects/{project_id}")
def unregister_project(project_id: str) -> dict:
    try:
        invalidate(project_id)
        registry.remove(project_id)
        return {"ok": True}
    except Exception as exc:
        raise _error(exc, 404)


@app.post("/api/projects/{project_id}/sources")
def add_source(project_id: str, body: SourceRequest) -> dict:
    try:
        root = project_path(project_id)
        config = ProjectConfig.load(root)
        path = validate_source_root(Path(body.path))
        name = body.name or path.name
        if any(source.name == name for source in config.source_roots):
            raise ValueError(f"Source name already exists: {name}")
        config.source_roots.append(SourceRoot(name=name, path=str(path)))
        config.save(root)
        invalidate(project_id)
        return _project_info(project_id)
    except Exception as exc:
        raise _error(exc)


@app.delete("/api/projects/{project_id}/sources/{source_name}")
def remove_source(project_id: str, source_name: str) -> dict:
    try:
        root = project_path(project_id)
        config = ProjectConfig.load(root)
        before = len(config.source_roots)
        config.source_roots = [source for source in config.source_roots if source.name != source_name]
        if len(config.source_roots) == before:
            raise KeyError(f"Unknown source: {source_name}")
        config.save(root)
        invalidate(project_id)
        return _project_info(project_id)
    except Exception as exc:
        raise _error(exc, 404)


@app.post("/api/projects/{project_id}/index")
async def index_project(project_id: str) -> dict:
    try:
        assistant = assistant_for(project_id, refresh=True)
        return await asyncio.to_thread(assistant.indexer.index_changed)
    except Exception as exc:
        raise _error(exc)

@app.get("/api/projects/{project_id}/index-status")
def get_index_status(project_id: str) -> dict:
    try:
        root = project_path(project_id)
        return IndexStatusStore(root / ".assistant/index_status.json").status.to_dict()
    except Exception as exc:
        raise _error(exc)


@app.get("/api/projects/{project_id}/conversations")
def list_conversations(project_id: str) -> list[dict]:
    try:
        return [
            {"id": item.id, "title": item.title, "path": str(item.path), "updated_at": item.updated_at}
            for item in _store(project_id).list()
        ]
    except Exception as exc:
        raise _error(exc)


@app.post("/api/projects/{project_id}/conversations")
def create_conversation(project_id: str, body: ConversationCreateRequest) -> dict:
    try:
        conversation = _store(project_id).create(body.title)
        return {"id": conversation.id, "title": conversation.title, "path": str(conversation.path)}
    except Exception as exc:
        raise _error(exc)


@app.get("/api/projects/{project_id}/conversations/{conversation_id}")
def get_conversation(project_id: str, conversation_id: str) -> dict:
    try:
        store = _store(project_id)
        conversation = store.find(conversation_id)
        return {
            "id": conversation.id,
            "title": conversation.title,
            "path": str(conversation.path),
            "markdown": conversation.path.read_text(encoding="utf-8", errors="replace"),
            "entries": [entry.to_dict() for entry in store.entries(conversation_id)],
        }
    except Exception as exc:
        raise _error(exc, 404)


@app.post("/api/projects/{project_id}/conversations/{conversation_id}/stream")
def stream_chat(project_id: str, conversation_id: str, body: ChatRequest):
    try:
        assistant = assistant_for(project_id)
    except Exception as exc:
        raise _error(exc)

    def generate() -> Iterable[str]:
        try:
            for event in assistant.answer_stream(conversation_id, body.message):
                if event[0] == "context":
                    compiled = event[1]
                    yield _sse(
                        "context",
                        {
                            "estimated_tokens": compiled.estimated_tokens,
                            "routed_repos": list(compiled.routed_repos),
                            "rag_sources": list(compiled.rag_sources),
                            "graph_sources": list(compiled.graph_sources),
                            "live_mainframe_sources": list(compiled.live_mainframe_sources),
                            "retrieval_queries": list(compiled.retrieval_queries),
                            "retrieval_actions": list(compiled.retrieval_actions),
                            "retrieval_warnings": list(compiled.retrieval_warnings),
                        },
                    )
                elif event[0] == "delta":
                    yield _sse("delta", {"text": event[1]})
                elif event[0] == "done":
                    yield _sse("done", {"ok": True})
        except Exception as exc:
            yield _sse("error", {"message": _safe_error_message(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{project_id}/conversations/{conversation_id}/implementation-brief")
async def prepare_implementation_brief(project_id: str, conversation_id: str, body: ImplementationBriefRequest) -> dict:
    try:
        assistant = assistant_for(project_id)
        brief, compiled, debug_path = await asyncio.to_thread(assistant.prepare_implementation_brief, conversation_id, body.focus)
        return {
            "brief": brief,
            "context": {
                "estimated_tokens": compiled.estimated_tokens,
                "routed_repos": list(compiled.routed_repos),
                "rag_sources": list(compiled.rag_sources),
                "graph_sources": list(compiled.graph_sources),
                "live_mainframe_sources": list(compiled.live_mainframe_sources),
                "retrieval_queries": list(compiled.retrieval_queries),
                "retrieval_actions": list(compiled.retrieval_actions),
                "retrieval_warnings": list(compiled.retrieval_warnings),
                "text": compiled.text,
                "debug_path": str(debug_path),
            },
        }
    except Exception as exc:
        raise _error(exc)


@app.post("/api/projects/{project_id}/context")
async def inspect_context(project_id: str, body: QueryRequest) -> dict:
    try:
        assistant = assistant_for(project_id)
        compiled = await asyncio.to_thread(assistant.compile_context, body.query, body.conversation_id, agentic=True)
        path = assistant.compiler.write_debug_snapshot(compiled)
        return {
            "estimated_tokens": compiled.estimated_tokens,
            "routed_repos": list(compiled.routed_repos),
            "rag_sources": list(compiled.rag_sources),
            "graph_sources": list(compiled.graph_sources),
            "live_mainframe_sources": list(compiled.live_mainframe_sources),
            "retrieval_queries": list(compiled.retrieval_queries),
            "retrieval_actions": list(compiled.retrieval_actions),
            "retrieval_warnings": list(compiled.retrieval_warnings),
            "text": compiled.text,
            "debug_path": str(path),
        }
    except Exception as exc:
        raise _error(exc)


@app.post("/api/projects/{project_id}/search")
async def search(project_id: str, body: QueryRequest) -> list[dict]:
    try:
        assistant = assistant_for(project_id)
        hits = await asyncio.to_thread(assistant.indexer.search, body.query)
        return [
            {"text": hit.text, "metadata": hit.metadata, "score": hit.score, "channels": list(hit.channels)}
            for hit in hits
        ]
    except Exception as exc:
        raise _error(exc)


@app.post("/api/projects/{project_id}/graph")
def graph(project_id: str, body: QueryRequest) -> list[dict]:
    try:
        path = project_path(project_id)
        graph_store = KnowledgeGraph(path / ".assistant/knowledge_graph.sqlite3")
        hits = graph_store.search(body.query, limit=20)
        return [
            {
                "node_type": hit.node_type,
                "name": hit.name,
                "source_path": hit.source_path,
                "neighbours": list(hit.neighbours),
                "metadata": hit.metadata,
            }
            for hit in hits
        ]
    except Exception as exc:
        raise _error(exc)
