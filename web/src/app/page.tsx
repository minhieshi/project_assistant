"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamChat } from "@/lib/api";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  ContextSummary,
  ConversationDetail,
  ConversationEntry,
  ConversationSummary,
  Project,
  Proposal,
  AppStatus,
  IndexStatus,
} from "@/lib/types";

function timeLabel(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" });
}

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [appStatus, setAppStatus] = useState<AppStatus | null>(null);
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null);
  const [projectId, setProjectId] = useState<string>("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string>("");
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [context, setContext] = useState<ContextSummary | null>(null);
  const [tab, setTab] = useState<"chat" | "project">("chat");
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"chat" | "propose">("chat");
  const [streamingText, setStreamingText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [importPath, setImportPath] = useState("");
  const [importName, setImportName] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newConversationTitle, setNewConversationTitle] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [projectRename, setProjectRename] = useState("");
  const [convertTargetId, setConvertTargetId] = useState("");
  const [convertSourceName, setConvertSourceName] = useState("");
  const [contextQuery, setContextQuery] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const currentProject = useMemo(() => projects.find((project) => project.id === projectId) ?? null, [projects, projectId]);

  const loadProjects = useCallback(async () => {
    const next = await api.projects();
    setProjects(next);
    setProjectId((current) => current && next.some((project) => project.id === current) ? current : next[0]?.id || "");
  }, []);

  const loadConversations = useCallback(async (pid: string) => {
    const next = await api.conversations(pid);
    setConversations(next);
    setConversationId((current) => (current && next.some((item) => item.id === current) ? current : next[0]?.id || ""));
  }, []);

  const loadConversation = useCallback(async (pid: string, cid: string) => {
    const [detail, proposalList] = await Promise.all([api.conversation(pid, cid), api.proposals(pid, cid)]);
    setConversation(detail);
    setProposals(proposalList);
  }, []);

  const loadIndexStatus = useCallback(async (pid: string) => {
    setIndexStatus(await api.indexStatus(pid));
  }, []);

  useEffect(() => {
    Promise.all([loadProjects(), api.status().then(setAppStatus)]).catch((e) => setError(String(e)));
  }, [loadProjects]);

  useEffect(() => {
    setConversation(null);
    setContext(null);
    const selected = projects.find((project) => project.id === projectId);
    setProjectRename(selected?.name ?? "");
    setConvertSourceName(selected?.name ?? "");
    setConvertTargetId((current) => current && current !== projectId ? current : projects.find((project) => project.id !== projectId)?.id ?? "");
    if (!projectId) {
      setConversations([]);
      setConversationId("");
      return;
    }
    loadConversations(projectId).catch((e) => setError(String(e)));
    loadIndexStatus(projectId).catch(() => setIndexStatus(null));
  }, [projectId, loadConversations, loadIndexStatus, projects]);

  useEffect(() => {
    setStreamingText("");
    if (!projectId || !conversationId) {
      setConversation(null);
      setProposals([]);
      return;
    }
    loadConversation(projectId, conversationId).catch((e) => setError(String(e)));
  }, [projectId, conversationId, loadConversation]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversation?.entries.length, streamingText]);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!newProjectName.trim()) return;
    setBusy(true); setError("");
    try {
      const project = await api.createProject(newProjectName.trim());
      setNewProjectName("");
      await loadProjects();
      setProjectId(project.id);
      setTab("project");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function importProject(event: FormEvent) {
    event.preventDefault();
    if (!importPath.trim()) return;
    setBusy(true); setError("");
    try {
      const project = await api.importProject(importPath.trim(), importName.trim() || undefined);
      setImportPath(""); setImportName("");
      await loadProjects();
      setProjectId(project.id);
      setTab("project");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function createConversation(event: FormEvent) {
    event.preventDefault();
    if (!projectId || !newConversationTitle.trim()) return;
    setBusy(true); setError("");
    try {
      const item = await api.createConversation(projectId, newConversationTitle.trim());
      setNewConversationTitle("");
      await loadConversations(projectId);
      setConversationId(item.id);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function send() {
    const text = message.trim();
    if (!projectId || !conversationId || !text || busy) return;
    setBusy(true); setError(""); setMessage(""); setStreamingText("");
    const optimistic: ConversationEntry = { title: "User", timestamp: new Date().toISOString(), body: text, role: "user" };
    setConversation((current) => current ? { ...current, entries: [...current.entries, optimistic] } : current);
    try {
      if (mode === "propose") {
        await api.propose(projectId, conversationId, text);
        await loadConversation(projectId, conversationId);
      } else {
        await streamChat(projectId, conversationId, text, {
          onContext: (value) => setContext(value),
          onDelta: (delta) => setStreamingText((current) => current + delta),
        });
        setStreamingText("");
        await loadConversation(projectId, conversationId);
      }
      await loadConversations(projectId);
    } catch (e) {
      setError(String(e));
      await loadConversation(projectId, conversationId).catch(() => undefined);
    } finally { setBusy(false); }
  }


  async function addSource(event: FormEvent) {
    event.preventDefault();
    if (!projectId || !sourcePath.trim()) return;
    setBusy(true); setError("");
    try {
      const updated = await api.addSource(projectId, sourcePath.trim(), sourceName.trim() || undefined);
      setProjects((items) => items.map((item) => item.id === updated.id ? updated : item));
      setSourcePath(""); setSourceName("");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function removeSource(name: string) {
    if (!projectId) return;
    if (!window.confirm(`Remove ${name} from this project's source repositories?\n\nThe repository itself will not be changed or deleted.`)) return;
    setBusy(true); setError("");
    try {
      const updated = await api.removeSource(projectId, name);
      setProjects((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function renameProject(event: FormEvent) {
    event.preventDefault();
    if (!projectId || !projectRename.trim()) return;
    setBusy(true); setError("");
    try {
      const updated = await api.renameProject(projectId, projectRename.trim());
      setProjects((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function convertProjectToSource(event: FormEvent) {
    event.preventDefault();
    if (!currentProject || currentProject.kind !== "imported" || !convertTargetId) return;
    const target = projects.find((project) => project.id === convertTargetId);
    if (!target) return;
    const confirmed = window.confirm(`Convert ${currentProject.name} into a source repository for ${target.name}?\n\nThe Git repository will not be moved or deleted. It will stop appearing as a standalone project.`);
    if (!confirmed) return;
    setBusy(true); setError("");
    try {
      const result = await api.convertProjectToSource(currentProject.id, target.id, convertSourceName.trim() || undefined);
      await loadProjects();
      setProjectId(result.target_project.id);
      setConversationId("");
      setTab("project");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function forgetImportedProject() {
    if (!currentProject || currentProject.kind !== "imported") return;
    const confirmed = window.confirm(`Forget ${currentProject.name} as a Project Assistant project?\n\nThis does not delete or move the Git repository.`);
    if (!confirmed) return;
    setBusy(true); setError("");
    try {
      await api.forgetProject(currentProject.id);
      setProjectId("");
      setConversationId("");
      await loadProjects();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function indexProject() {
    if (!projectId) return;
    setBusy(true); setError("");
    const pid = projectId;
    const poll = window.setInterval(() => { void loadIndexStatus(pid).catch(() => undefined); }, 700);
    try {
      await api.indexProject(pid);
      await loadIndexStatus(pid);
    } catch (e) {
      setError(String(e));
      await loadIndexStatus(pid).catch(() => undefined);
    } finally {
      window.clearInterval(poll);
      setBusy(false);
    }
  }

  async function proposalAction(proposal: Proposal, action: "approve-plan" | "reject", approvedActions: string[] = []) {
    if (!projectId) return;
    setBusy(true); setError("");
    try {
      if (action === "approve-plan") await api.approvePlan(projectId, proposal.id, approvedActions);
      else await api.reject(projectId, proposal.id);
      if (conversationId) await loadConversation(projectId, conversationId);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function inspectContext() {
    if (!projectId || !contextQuery.trim()) return;
    setBusy(true); setError("");
    try {
      setContext(await api.inspectContext(projectId, contextQuery.trim(), conversationId || undefined));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">Project Assistant <span className="small">v0.7.3</span></div>
        {appStatus && <div className="notice" style={{ marginBottom: 12 }}>Projects: {appStatus.projects_root}<br />Portkey: {appStatus.portkey.base_url_configured ? "URL configured" : "URL missing"}</div>}

        <div className="section-title">Projects</div>
        {projects.map((project) => (
          <button key={project.id} className={`nav-item ${project.id === projectId ? "active" : ""}`} onClick={() => setProjectId(project.id)} title={project.path}>
            {project.name} <span className="small">· {project.kind}</span>
          </button>
        ))}

        {projects.length === 0 && <div className="notice" style={{ marginBottom: 10 }}>No projects found yet. Create a managed project or import an existing Git repo.</div>}

        <form className="form-stack" onSubmit={createProject} style={{ marginTop: 10 }}>
          <input className="input" value={newProjectName} onChange={(e) => setNewProjectName(e.target.value)} placeholder="New project name" />
          <button className="btn primary" disabled={busy || !newProjectName.trim()}>+ Create project repo</button>
        </form>

        <form className="form-stack" onSubmit={importProject} style={{ marginTop: 10 }}>
          <input className="input" value={importPath} onChange={(e) => setImportPath(e.target.value)} placeholder="/absolute/path/to/existing/git/repo" />
          <input className="input" value={importName} onChange={(e) => setImportName(e.target.value)} placeholder="Optional project name" />
          <button className="btn" disabled={busy || !importPath.trim()}>Import existing repo as project</button>
          <div className="small">Use this only when the repo itself should own project conversations/settings. If it is reference source code for another project, add it under that project's Sources instead.</div>
        </form>

        {projectId && <>
          <div className="section-title">Conversations</div>
          {conversations.map((item) => (
            <button key={item.id} className={`nav-item ${item.id === conversationId ? "active" : ""}`} onClick={() => { setConversationId(item.id); setTab("chat"); }} title={timeLabel(item.updated_at)}>
              {item.title}
            </button>
          ))}
          <form className="form-stack" onSubmit={createConversation} style={{ marginTop: 10 }}>
            <input className="input" value={newConversationTitle} onChange={(e) => setNewConversationTitle(e.target.value)} placeholder="New conversation" />
            <button className="btn" disabled={busy || !newConversationTitle.trim()}>+ New chat</button>
          </form>
        </>}
        {error && <div className="error" style={{ marginTop: 14 }}>{error}</div>}
      </aside>

      <main className="main">
        <header className="header">
          <h1>{currentProject ? currentProject.name : "No project selected"}{conversation ? ` / ${conversation.title}` : ""}</h1>
          <div className="tabs">
            <button className={`tab ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>Chat</button>
            <button className={`tab ${tab === "project" ? "active" : ""}`} onClick={() => setTab("project")}>Project</button>
          </div>
        </header>

        {tab === "chat" ? (
          <>
            <section className="chat-scroll">
              {!conversation ? <div className="empty">{projectId ? "Create or select a conversation." : "Register a local project to begin."}</div> : <>
                {conversation.entries.map((entry, index) => <Message key={`${entry.timestamp}-${index}`} entry={entry} />)}
                {streamingText && <Message entry={{ title: "Assistant", timestamp: "", body: streamingText, role: "assistant" }} />}
                <div ref={bottomRef} />
              </>}
            </section>
            {conversation && <div className="composer">
              <div className="composer-box">
                <textarea className="textarea" value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
                }} placeholder={mode === "propose" ? "Describe the code/configuration change. It will be written as a proposal only." : "Ask about this project..."} />
                <div className="composer-actions">
                  <div className="row wrap">
                    <div className="mode">
                      <button className={mode === "chat" ? "active" : ""} onClick={() => setMode("chat")}>Chat</button>
                      <button className={mode === "propose" ? "active" : ""} onClick={() => setMode("propose")}>Propose change</button>
                    </div>
                  </div>
                  <button className="btn primary" onClick={() => void send()} disabled={busy || !message.trim()}>{busy ? "Working…" : mode === "propose" ? "Write proposal" : "Send"}</button>
                  <span className="small">Tip: macOS Dictation works directly in the message box using your configured Dictation shortcut.</span>
                </div>
              </div>
            </div>}
          </>
        ) : (
          <ProjectPanel
            project={currentProject}
            projects={projects}
            busy={busy}
            sourcePath={sourcePath}
            sourceName={sourceName}
            setSourcePath={setSourcePath}
            setSourceName={setSourceName}
            addSource={addSource}
            removeSource={removeSource}
            indexProject={indexProject}
            projectRename={projectRename}
            setProjectRename={setProjectRename}
            renameProject={renameProject}
            convertTargetId={convertTargetId}
            setConvertTargetId={setConvertTargetId}
            convertSourceName={convertSourceName}
            setConvertSourceName={setConvertSourceName}
            convertProjectToSource={convertProjectToSource}
            forgetImportedProject={forgetImportedProject}
            indexStatus={indexStatus}
          />
        )}
      </main>

      <aside className="inspector">
        <div className="section-title" style={{ marginTop: 0 }}>Context</div>
        <div className="form-stack">
          <textarea className="textarea" value={contextQuery} onChange={(e) => setContextQuery(e.target.value)} placeholder="Inspect retrieval for a question..." />
          <button className="btn" onClick={() => void inspectContext()} disabled={!projectId || busy || !contextQuery.trim()}>Compile context</button>
        </div>
        <ContextPanel context={context} />

        <div className="section-title">Change proposals</div>
        {proposals.length === 0 ? <div className="notice">No proposals for this conversation.</div> : proposals.map((proposal) => (
          <ProposalCard key={proposal.id} proposal={proposal} projectId={projectId} busy={busy} onAction={proposalAction} onApplied={async () => { if (conversationId) await loadConversation(projectId, conversationId); }} />
        ))}
      </aside>
    </div>
  );
}

function Message({ entry }: { entry: ConversationEntry }) {
  const role = entry.role === "event" ? "event" : entry.role;
  const markdown = entry.role !== "user";
  return <div className={`message ${role}`}>
    <div className="message-label">{entry.title}{entry.timestamp ? ` · ${timeLabel(entry.timestamp)}` : ""}</div>
    {markdown ? (
      <div className="message-body markdown">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
          }}
        >{entry.body}</ReactMarkdown>
      </div>
    ) : <div className="message-body">{entry.body}</div>}
  </div>;
}

function ProjectPanel(props: {
  project: Project | null;
  projects: Project[];
  busy: boolean;
  sourcePath: string;
  sourceName: string;
  setSourcePath: (value: string) => void;
  setSourceName: (value: string) => void;
  addSource: (event: FormEvent) => void;
  removeSource: (name: string) => void;
  indexProject: () => void;
  projectRename: string;
  setProjectRename: (value: string) => void;
  renameProject: (event: FormEvent) => void;
  convertTargetId: string;
  setConvertTargetId: (value: string) => void;
  convertSourceName: string;
  setConvertSourceName: (value: string) => void;
  convertProjectToSource: (event: FormEvent) => void;
  forgetImportedProject: () => void;
  indexStatus: IndexStatus | null;
}) {
  const { project } = props;
  if (!project) return <div className="empty">Select a project.</div>;
  const conversionTargets = props.projects.filter((item) => item.id !== project.id);
  return <section className="chat-scroll">
    <h2 style={{ marginTop: 0 }}>Project</h2>
    <div className="card">
      <h3>Project repository</h3>
      <div className="small">{project.kind === "managed" ? "Managed local Git repo" : "Imported existing Git repo"}</div>
      <div className="path" style={{ marginTop: 6 }}>{project.path}</div>
    </div>

    <div className="card" style={{ marginTop: 12 }}>
      <h3>Project settings</h3>
      <form className="row wrap" onSubmit={props.renameProject}>
        <input className="input grow" value={props.projectRename} onChange={(e) => props.setProjectRename(e.target.value)} placeholder="Project name" />
        <button className="btn" disabled={props.busy || !props.projectRename.trim() || props.projectRename.trim() === project.name}>Rename</button>
      </form>

      {project.kind === "imported" && <div className="correction-box">
        <h3>Mistakenly imported this as a project?</h3>
        <div className="small">Convert it into a source repository for another project. The Git repo is not copied, moved or deleted; it simply stops appearing as a standalone project.</div>
        {conversionTargets.length > 0 ? <form className="form-stack" onSubmit={props.convertProjectToSource} style={{ marginTop: 10 }}>
          <select className="input" value={props.convertTargetId} onChange={(e) => props.setConvertTargetId(e.target.value)}>
            <option value="">Choose target project…</option>
            {conversionTargets.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.kind}</option>)}
          </select>
          <input className="input" value={props.convertSourceName} onChange={(e) => props.setConvertSourceName(e.target.value)} placeholder="Source name" />
          <button className="btn primary" disabled={props.busy || !props.convertTargetId}>Convert to source repository</button>
        </form> : <div className="notice">Create or import the real target project first, then return here to convert this repo into one of its sources.</div>}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn danger" type="button" disabled={props.busy} onClick={props.forgetImportedProject}>Forget as project</button>
          <span className="small">This only removes the Project Assistant catalogue entry.</span>
        </div>
      </div>}
    </div>

    <div className="row" style={{ justifyContent: "space-between", marginTop: 24 }}><h3>Source repositories</h3><button className="btn" onClick={props.indexProject} disabled={props.busy}>Reindex changed files</button></div>
    <IndexVisibility status={props.indexStatus} />
    <div className="source-list">
      {project.sources.length === 0 && <div className="notice">No external source repos registered yet.</div>}
      {project.sources.map((source) => <div className="source-row row" style={{ justifyContent: "space-between" }} key={source.name}>
        <div style={{ minWidth: 0 }}><div className="source-name">{source.name}</div><div className="path">{source.path}</div></div>
        <button className="btn danger" type="button" disabled={props.busy} onClick={() => props.removeSource(source.name)}>Remove</button>
      </div>)}
    </div>
    <form onSubmit={props.addSource} className="form-stack" style={{ marginTop: 18, maxWidth: 600 }}>
      <input className="input" value={props.sourcePath} onChange={(e) => props.setSourcePath(e.target.value)} placeholder="/absolute/path/to/repo" />
      <input className="input" value={props.sourceName} onChange={(e) => props.setSourceName(e.target.value)} placeholder="Optional repo name" />
      <button className="btn primary" disabled={props.busy || !props.sourcePath.trim()}>Add source repo</button>
    </form>
    <div className="project-scroll-end" aria-hidden="true" />
  </section>;
}

function IndexVisibility({ status }: { status: IndexStatus | null }) {
  if (!status) return <div className="notice" style={{ marginBottom: 12 }}>No index run recorded yet.</div>;
  const repoRows = Object.entries(status.repos);
  const reasonRows = Object.entries(status.skip_reasons).sort((a, b) => b[1] - a[1]);
  const localOnlyRows = Object.entries(status.local_only_reasons ?? {}).sort((a, b) => b[1] - a[1]);
  return <div className="card" style={{ marginBottom: 14 }}>
    <div className="row wrap" style={{ justifyContent: "space-between" }}>
      <div>
        <h3>Index visibility</h3>
        <div className="small">State: <strong>{status.state}</strong>{status.finished_at ? ` · ${timeLabel(status.finished_at)}` : ""}</div>
      </div>
      <div className="row wrap">
        <span className="badge">{status.scanned} scanned</span>
        <span className="badge">{status.chunks} chunks</span>
        <span className="badge">{status.skipped} skipped</span>
        <span className="badge">{status.local_only} local-only</span>
      </div>
    </div>
    {status.state === "running" && <div className="notice" style={{ marginTop: 10 }}>Indexing {status.current_repo ?? ""}{status.current_file ? ` / ${status.current_file}` : ""}</div>}
    {status.last_error && <div className="error" style={{ marginTop: 10 }}>{status.last_error}</div>}
    {repoRows.length > 0 && <div style={{ marginTop: 12 }}>
      <div className="small" style={{ marginBottom: 6 }}>Per repository</div>
      {repoRows.map(([name, stats]) => <div className="source-row row wrap" style={{ justifyContent: "space-between" }} key={name}>
        <strong>{name}</strong>
        <span className="small">{stats.scanned} scanned · {stats.indexed} indexed · {stats.unchanged} unchanged · {stats.skipped} skipped · {stats.local_only} local-only · {stats.chunks} chunks</span>
      </div>)}
    </div>}
    {reasonRows.length > 0 && <div style={{ marginTop: 12 }}>
      <div className="small" style={{ marginBottom: 6 }}>Skipped automatically</div>
      <div className="row wrap">{reasonRows.map(([reason, count]) => <span className="badge" key={reason}>{reason}: {count}</span>)}</div>
    </div>}
    {localOnlyRows.length > 0 && <div style={{ marginTop: 12 }}>
      <div className="small" style={{ marginBottom: 6 }}>Kept local-only</div>
      <div className="row wrap">{localOnlyRows.map(([reason, count]) => <span className="badge" key={reason}>{reason}: {count}</span>)}</div>
    </div>}
    {status.recent_skips.length > 0 && <details style={{ marginTop: 12 }}>
      <summary className="small">Recent skipped files</summary>
      <ul className="context-list">{status.recent_skips.slice(-15).reverse().map((item, idx) => <li key={`${item.repo}-${item.path}-${idx}`}><strong>{item.repo}</strong> · {item.path} <span className="small">({item.reason})</span></li>)}</ul>
    </details>}
  </div>;
}

function ContextPanel({ context }: { context: ContextSummary | null }) {
  if (!context) return <div className="notice">Context used by the most recent chat will appear here, or compile one manually.</div>;
  return <>
    <div className="card" style={{ marginTop: 12 }}>
      <h3>{context.estimated_tokens.toLocaleString("en-AU")} estimated tokens</h3>
      <div className="small">Routed repositories</div>
      <div className="row wrap" style={{ marginTop: 7 }}>{context.routed_repos.map((repo) => <span className="badge" key={repo}>{repo}</span>)}</div>
    </div>
    {context.retrieval_queries?.length > 0 && <div className="card"><h3>Retrieval queries</h3><ul className="context-list">{context.retrieval_queries.slice(0, 10).map((query, idx) => <li key={`${idx}-${query}`}>{query}</li>)}</ul></div>}
    {context.retrieval_actions?.length > 0 && <div className="card"><h3>Agent retrieval</h3><ul className="context-list">{context.retrieval_actions.map((action, idx) => <li key={`${idx}-${action}`}>{action}</li>)}</ul></div>}
    {context.retrieval_warnings?.length > 0 && <div className="card"><h3>Retrieval warnings</h3><ul className="context-list">{context.retrieval_warnings.map((warning, idx) => <li key={`${idx}-${warning}`}>{warning}</li>)}</ul></div>}
    <div className="card"><h3>Retrieved source</h3><ul className="context-list">{context.rag_sources.slice(0, 30).map((source) => <li key={source}>{source}</li>)}</ul></div>
    {context.graph_sources.length > 0 && <div className="card"><h3>Graph sources</h3><ul className="context-list">{context.graph_sources.slice(0, 20).map((source) => <li key={source}>{source}</li>)}</ul></div>}
    {context.text && <div className="card"><h3>Compiled context</h3><div className="context-text">{context.text}</div>{context.debug_path && <div className="path" style={{ marginTop: 8 }}>{context.debug_path}</div>}</div>}
  </>;
}

function approvalItemsFromPlan(plan: string): string[] {
  const seen = new Set<string>();
  const items: string[] = [];
  for (const line of plan.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:[-*+]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s*)?(.+?)\s*$/);
    if (!match) continue;
    const item = match[1].replace(/^\*\*(.+)\*\*$/, "$1").trim();
    if (!item || seen.has(item)) continue;
    seen.add(item);
    items.push(item);
    if (items.length >= 12) break;
  }
  return items.length > 0 ? items : ["Approve the proposed implementation plan as written"];
}

function ProposalCard({ proposal, projectId, busy, onAction, onApplied }: { proposal: Proposal; projectId: string; busy: boolean; onAction: (proposal: Proposal, action: "approve-plan" | "reject", approvedActions?: string[]) => void; onApplied: () => Promise<void> }) {
  const [patchPath, setPatchPath] = useState("");
  const [repoPath, setRepoPath] = useState(proposal.repo_path ?? "");
  const [localError, setLocalError] = useState("");
  const [working, setWorking] = useState(false);
  const planItems = useMemo(() => approvalItemsFromPlan(proposal.plan), [proposal.plan]);
  const [selectedPlanActions, setSelectedPlanActions] = useState<string[]>([]);
  const [diffChecks, setDiffChecks] = useState<string[]>([]);
  const [applyConfirmed, setApplyConfirmed] = useState(false);

  useEffect(() => {
    if (proposal.status === "pending") setSelectedPlanActions([]);
    if (proposal.status === "patch_pending") setDiffChecks([]);
    if (proposal.status === "patch_approved") setApplyConfirmed(false);
  }, [proposal.id, proposal.status]);

  function togglePlanAction(item: string) {
    setSelectedPlanActions((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item]);
  }

  function toggleDiffCheck(item: string) {
    setDiffChecks((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item]);
  }

  async function run(action: () => Promise<unknown>) {
    setWorking(true); setLocalError("");
    try { await action(); await onApplied(); }
    catch (e) { setLocalError(String(e)); }
    finally { setWorking(false); }
  }

  const statusLabel = proposal.status.replaceAll("_", " ");
  const hasPatch = Boolean(proposal.patch_text);

  return <div className="card">
    <div className="row" style={{ justifyContent: "space-between" }}><h3>{proposal.id}</h3><span className={`badge ${proposal.status}`}>{statusLabel}</span></div>
    <div className="small" style={{ marginBottom: 6 }}>{timeLabel(proposal.created_at)}</div>
    <pre>{proposal.plan}</pre>
    {proposal.approved_actions && proposal.approved_actions.length > 0 && proposal.status !== "pending" && <div className="approval-summary"><strong>Approved plan scope</strong><ul>{proposal.approved_actions.map((item) => <li key={item}>✓ {item}</li>)}</ul></div>}

    {proposal.status === "pending" && <div className="approval-panel" style={{ marginTop: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <strong>Approval scope</strong>
        <button className="btn compact" type="button" disabled={busy || working} onClick={() => setSelectedPlanActions(selectedPlanActions.length === planItems.length ? [] : [...planItems])}>{selectedPlanActions.length === planItems.length ? "Clear all" : "Select all"}</button>
      </div>
      <div className="small" style={{ marginTop: 4 }}>Tick only the actions you want to approve for candidate-diff preparation. This does not permit source changes.</div>
      <div className="approval-checks">{planItems.map((item) => <label className="approval-check" key={item}><input type="checkbox" checked={selectedPlanActions.includes(item)} onChange={() => togglePlanAction(item)} /><span>{item}</span></label>)}</div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={busy || working || selectedPlanActions.length === 0} onClick={() => onAction(proposal, "approve-plan", selectedPlanActions)}>Approve selected actions</button>
        <button className="btn danger" disabled={busy || working} onClick={() => onAction(proposal, "reject")}>Reject</button>
      </div>
    </div>}

    {proposal.status === "plan_approved" && <div className="form-stack" style={{ marginTop: 12 }}>
      <div className="small">Plan approval permits creation/staging of a candidate diff only. Staging runs <code>git apply --check</code>, copies the exact diff into private assistant storage and binds it to the current Git HEAD.</div>
      <input className="input" value={patchPath} onChange={(e) => setPatchPath(e.target.value)} placeholder="Local patch file path" />
      <input className="input" value={repoPath} onChange={(e) => setRepoPath(e.target.value)} placeholder="Registered repo path" />
      <button className="btn primary" disabled={busy || working || !patchPath.trim() || !repoPath.trim()} onClick={() => void run(() => api.stagePatch(projectId, proposal.id, patchPath.trim(), repoPath.trim()))}>{working ? "Validating…" : "Validate & stage diff"}</button>
      <button className="btn danger" disabled={busy || working} onClick={() => onAction(proposal, "reject")}>Reject proposal</button>
    </div>}

    {(proposal.status === "patch_pending" || proposal.status === "patch_approved" || proposal.status === "applied") && <div style={{ marginTop: 12 }}>
      <div className="small">Repo: {proposal.repo_path ?? "-"}</div>
      <div className="small">Base: {proposal.base_commit?.slice(0, 12) ?? "-"}</div>
      <div className="small" style={{ wordBreak: "break-all" }}>SHA-256: {proposal.patch_sha256 ?? "-"}</div>
      {hasPatch && <div className="context-text" style={{ marginTop: 8 }}>{proposal.patch_text}</div>}
    </div>}

    {proposal.status === "patch_pending" && <div className="approval-panel" style={{ marginTop: 12 }}>
      <strong>Approve exact diff</strong>
      <div className="small" style={{ marginTop: 4 }}>All confirmations below are required before the immutable staged diff can be approved.</div>
      <div className="approval-checks">{[
        "I have reviewed the exact diff shown above",
        `I approve changes to ${proposal.repo_path ?? "the registered repository"}`,
        `I approve this exact SHA-256 against base commit ${(proposal.base_commit ?? "").slice(0, 12) || "shown above"}`,
      ].map((item) => <label className="approval-check" key={item}><input type="checkbox" checked={diffChecks.includes(item)} onChange={() => toggleDiffCheck(item)} /><span>{item}</span></label>)}</div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={busy || working || diffChecks.length < 3} onClick={() => void run(() => api.approvePatch(projectId, proposal.id, diffChecks))}>Approve exact diff</button>
        <button className="btn danger" disabled={busy || working} onClick={() => onAction(proposal, "reject")}>Reject</button>
      </div>
    </div>}

    {proposal.patch_approval_checks && proposal.patch_approval_checks.length > 0 && (proposal.status === "patch_approved" || proposal.status === "applied") && <div className="approval-summary"><strong>Diff approval recorded</strong><ul>{proposal.patch_approval_checks.map((item) => <li key={item}>✓ {item}</li>)}</ul></div>}

    {proposal.status === "patch_approved" && <div className="approval-panel" style={{ marginTop: 10 }}>
      <div className="small">Only the hashed diff shown above can be applied. The backend rechecks the hash, repo, HEAD commit and <code>git apply --check</code> immediately before mutation.</div>
      <label className="approval-check" style={{ marginTop: 10 }}><input type="checkbox" checked={applyConfirmed} onChange={(event) => setApplyConfirmed(event.target.checked)} /><span>Apply this already-approved exact diff to the working tree now</span></label>
      <button className="btn primary" disabled={busy || working || !applyConfirmed} onClick={() => void run(() => api.applyPatch(projectId, proposal.id))}>{working ? "Applying…" : "Apply approved diff"}</button>
    </div>}

    {localError && <div className="error" style={{ marginTop: 8 }}>{localError}</div>}
  </div>;
}

