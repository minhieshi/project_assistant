from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import ProjectConfig, SourceRoot, init_project
from .security import validate_source_root
from .mcp_registry import MCPServerRegistry
from .workspace import WorkspaceRegistry


def _project(value: str) -> Path:
    return Path(value).expanduser().resolve()


def main() -> None:
    parser = argparse.ArgumentParser(prog="project-assistant")
    parser.add_argument("--project", default=".", help="Project assistant directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("projects", help="List discovered managed/imported projects")

    sub.add_parser("mcp-list", help="List configured MCP servers and connection state")

    p_mcp_add = sub.add_parser("mcp-add", help="Add or update a remote Streamable HTTP MCP server")
    p_mcp_add.add_argument("server_id")
    p_mcp_add.add_argument("url")
    p_mcp_add.add_argument("--name")
    p_mcp_add.add_argument("--disabled", action="store_true")
    p_mcp_add.add_argument("--tls-compat", action="store_true", help="Allow trusted corporate CA chains that fail Python VERIFY_X509_STRICT; verification and hostname checks remain enabled")

    p_mcp_local = sub.add_parser("mcp-add-local", help="Add or update a local stdio MCP server")
    p_mcp_local.add_argument("server_id")
    p_mcp_local.add_argument("executable")
    p_mcp_local.add_argument("args", nargs="*")
    p_mcp_local.add_argument("--name")
    p_mcp_local.add_argument("--cwd")
    p_mcp_local.add_argument("--disabled", action="store_true")

    p_mcp_connect = sub.add_parser("mcp-connect", help="Connect/authenticate and discover an MCP server")
    p_mcp_connect.add_argument("server_id")

    p_mcp_tools = sub.add_parser("mcp-tools", help="Connect and list tools/resources exposed by an MCP server")
    p_mcp_tools.add_argument("server_id")

    p_mcp_logout = sub.add_parser("mcp-logout", help="Delete locally stored OAuth state for an MCP server")
    p_mcp_logout.add_argument("server_id")

    p_mcp_remove = sub.add_parser("mcp-remove", help="Remove an MCP server and its stored OAuth state")
    p_mcp_remove.add_argument("server_id")

    p_mcp_enable = sub.add_parser("mcp-enable", help="Enable a configured MCP server")
    p_mcp_enable.add_argument("server_id")
    p_mcp_disable = sub.add_parser("mcp-disable", help="Disable a configured MCP server")
    p_mcp_disable.add_argument("server_id")

    p_project_create = sub.add_parser("project-create", help="Create a managed local Git-backed project")
    p_project_create.add_argument("name")

    p_project_import = sub.add_parser("project-import", help="Import an existing Git repository as a project")
    p_project_import.add_argument("path")
    p_project_import.add_argument("--name")

    p_project_rename = sub.add_parser("project-rename", help="Rename a discovered project")
    p_project_rename.add_argument("project_id")
    p_project_rename.add_argument("name")

    p_project_convert = sub.add_parser("project-convert-to-source", help="Convert an imported project into a source of another project")
    p_project_convert.add_argument("project_id")
    p_project_convert.add_argument("target_project_id")
    p_project_convert.add_argument("--name")

    p_project_forget = sub.add_parser("project-forget", help="Forget an imported project without deleting its repository")
    p_project_forget.add_argument("project_id")

    p_init = sub.add_parser("init")
    p_init.add_argument("--name", required=True)

    p_source = sub.add_parser("source-add")
    p_source.add_argument("path")
    p_source.add_argument("--name")

    sub.add_parser("index")
    p_consolidate = sub.add_parser("consolidate", help="Run conversation consolidation and update user memory when due")
    p_consolidate.add_argument("--force", action="store_true", help="Run even when the 24-hour interval has not elapsed")
    sub.add_parser("embedding-test", help="Test the configured embedding route with a fixed non-sensitive string")
    sub.add_parser("chat-test", help="Test the configured chat route, reasoning level, and streaming")

    p_new = sub.add_parser("chat-new")
    p_new.add_argument("title")

    p_chat = sub.add_parser("chat")
    p_chat.add_argument("conversation_id")
    p_chat.add_argument("message")
    p_chat.add_argument("--guided", action="store_true", help="Use step-by-step guided implementation mode")

    p_search = sub.add_parser("search")
    p_search.add_argument("query")

    p_graph = sub.add_parser("graph")
    p_graph.add_argument("query")

    p_route = sub.add_parser("route")
    p_route.add_argument("query")

    p_context = sub.add_parser("context")
    p_context.add_argument("query")
    p_context.add_argument("--conversation")

    args = parser.parse_args()

    if args.command == "projects":
        for project in WorkspaceRegistry().list():
            print(f"{project.id}\t{project.kind}\t{project.name}\t{project.path}")
        return

    if args.command.startswith("mcp-"):
        mcp_registry = MCPServerRegistry()
        if args.command == "mcp-add":
            server = mcp_registry.add_remote(
                args.server_id, args.url, name=args.name, enabled=not args.disabled, tls_compat=args.tls_compat
            )
            print(json.dumps(server.to_dict(), indent=2))
            return
        if args.command == "mcp-add-local":
            server = mcp_registry.add_local(
                args.server_id, args.executable, args=args.args, cwd=args.cwd,
                name=args.name, enabled=not args.disabled,
            )
            print(json.dumps(server.to_dict(), indent=2))
            return
        if args.command in {"mcp-enable", "mcp-disable"}:
            server = mcp_registry.set_enabled(args.server_id, args.command == "mcp-enable")
            print(json.dumps(server.to_dict(), indent=2))
            return

        from .mcp_client import MCPManager
        manager = MCPManager(registry=mcp_registry)
        if args.command == "mcp-list":
            for item in manager.configured():
                target = item.get("url") or " ".join([item.get("command", ""), *item.get("args", [])]).strip()
                print(f"{item['id']}\t{item['type']}\t{item['status']}\t{target}")
            return
        if args.command in {"mcp-connect", "mcp-tools"}:
            result = asyncio.run(manager.probe(args.server_id))
            if args.command == "mcp-connect":
                print(json.dumps(result.to_dict(), indent=2))
            else:
                print(f"status={result.status} protocol={result.protocol_version or 'unknown'} server={result.server_name or args.server_id}")
                if result.error:
                    print(f"error={result.error}")
                print("\nTools:")
                for tool in result.tools:
                    hint = " read-only-hint" if tool.read_only_hint is True else ""
                    print(f"- {tool.name}{hint}: {tool.description or tool.title or ''}")
                print("\nResources:")
                for resource in result.resources:
                    print(f"- {resource.uri}: {resource.description or resource.title or resource.name or ''}")
            return
        if args.command == "mcp-logout":
            manager.logout(args.server_id)
            print(f"Logged out of {args.server_id}")
            return
        if args.command == "mcp-remove":
            try:
                manager.logout(args.server_id)
            finally:
                mcp_registry.remove(args.server_id)
            print(f"Removed {args.server_id}")
            return

    if args.command == "project-create":
        project = WorkspaceRegistry().create(args.name)
        print(project.path)
        return

    if args.command == "project-import":
        project = WorkspaceRegistry().import_repo(Path(args.path), args.name)
        print(project.path)
        return

    if args.command == "project-rename":
        project = WorkspaceRegistry().rename(args.project_id, args.name)
        print(f"{project.id}\t{project.name}\t{project.path}")
        return

    if args.command == "project-convert-to-source":
        target, source = WorkspaceRegistry().convert_imported_to_source(args.project_id, args.target_project_id, args.name)
        print(f"Converted to source {source.name}: {source.path}")
        print(f"Target project: {target.id} {target.name}")
        return

    if args.command == "project-forget":
        WorkspaceRegistry().remove(args.project_id)
        print(f"Forgot imported project {args.project_id}")
        return

    project_dir = _project(args.project)

    if args.command == "embedding-test":
        from .config import PortkeySettings
        from .portkey import get_embedding_function

        settings = PortkeySettings.from_env()
        embeddings = get_embedding_function(settings)
        vector = embeddings.embed_query("Project Assistant embedding connectivity test")
        print(f"OK model={settings.embedding_model} dimensions={len(vector)}")
        return

    if args.command == "chat-test":
        from .config import PortkeySettings
        from .portkey import PortkeyChatModel

        settings = PortkeySettings.from_env()
        model = PortkeyChatModel(settings)
        system = "You are a connectivity test. Follow the user instruction exactly."
        user = "Reply with exactly PROJECT_ASSISTANT_OK"
        response = model.complete(system, user).strip()
        streamed = "".join(model.stream(system, user)).strip()
        print(f"model={settings.chat_model}")
        print(f"api_mode={settings.api_mode}")
        print(f"reasoning={settings.reasoning_effort}")
        print(f"non_streaming={response}")
        print(f"streaming={streamed}")
        return

    if args.command == "init":
        config = init_project(project_dir, args.name)
        print(config.save(project_dir))
        return

    if args.command == "source-add":
        config = ProjectConfig.load(project_dir)
        path = validate_source_root(Path(args.path))
        name = args.name or path.name
        if any(s.name == name for s in config.source_roots):
            raise SystemExit(f"Source name already exists: {name}")
        config.source_roots.append(SourceRoot(name=name, path=str(path)))
        config.save(project_dir)
        print(f"Added source {name}: {path}")
        return

    # RAG/model dependencies are loaded only for commands that actually need
    # them. Project discovery remains available during partial configuration.
    from .assistant import ProjectAssistant
    assistant = ProjectAssistant.build(project_dir)

    if args.command == "index":
        print(json.dumps(assistant.indexer.index_changed(), indent=2))
    elif args.command == "consolidate":
        result = assistant.consolidate_memory(force=args.force)
        print(json.dumps({
            "ran": result.ran,
            "entries": result.entries,
            "reason": result.reason,
            "consolidation_path": str(result.consolidation_path) if result.consolidation_path else None,
            "user_memory_path": str(result.user_memory_path) if result.user_memory_path else None,
        }, indent=2))
    elif args.command == "chat-new":
        conv = assistant.conversations.create(args.title)
        print(conv.id)
        print(conv.path)
    elif args.command == "chat":
        mode = "guided" if args.guided else "chat"
        print(assistant.answer(args.conversation_id, args.message, mode=mode))
    elif args.command == "search":
        for hit in assistant.indexer.search(args.query):
            line = ""
            if hit.metadata.get("start_line"):
                line = f":{hit.metadata.get('start_line')}-{hit.metadata.get('end_line')}"
            symbol = f" symbol={hit.metadata.get('symbol')}" if hit.metadata.get("symbol") else ""
            print(f"{hit.metadata.get('repo')}:{hit.metadata.get('relative_path')}{line}{symbol} score={hit.score} via={','.join(hit.channels)}")
            print(hit.text[:600].replace("\n", " "))
            print()
    elif args.command == "graph":
        for hit in assistant.indexer.graph.search(args.query, limit=assistant.config.graph_top_n):
            print(f"{hit.node_type}: {hit.name} ({hit.source_path or 'cross-file'})")
            for n in hit.neighbours:
                print(f"  {n}")
    elif args.command == "route":
        graph_hits = assistant.indexer.graph.search(args.query, limit=assistant.config.graph_top_n)
        vector_hits = assistant.indexer.vector_search(args.query, k=assistant.config.vector_top_k)
        lexical_hits = assistant.indexer.lexical_search(args.query, k=assistant.config.lexical_top_k)
        for route in assistant.indexer.catalog.route(args.query, lexical_hits, vector_hits, graph_hits, assistant.config.repo_route_top_n):
            print(f"{route.name}: {route.score:.3f} ({', '.join(route.reasons) or 'fallback'})")
    elif args.command == "context":
        compiled = assistant.compile_context(args.query, args.conversation, agentic=True)
        path = assistant.compiler.write_debug_snapshot(compiled)
        print(f"Estimated tokens: {compiled.estimated_tokens}")
        print(f"Snapshot: {path}")
        print(compiled.text)


if __name__ == "__main__":
    main()
