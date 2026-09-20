"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamChat } from "@/lib/api";
import type {
  ContextSummary,
  ConversationDetail,
  ConversationEntry,
  ConversationSummary,
  Project,
  Proposal,
} from "@/lib/types";

function timeLabel(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" });
}

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
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
  const [registerPath, setRegisterPath] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newConversationTitle, setNewConversationTitle] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [contextQuery, setContextQuery] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const currentProject = useMemo(() => projects.find((project) => project.id === projectId) ?? null, [projects, projectId]);

  const loadProjects = useCallback(async () => {
    const next = await api.projects();
    setProjects(next);
    setProjectId((current) => current || next[0]?.id || "");
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

  useEffect(() => {
    loadProjects().catch((e) => setError(String(e)));
  }, [loadProjects]);

  useEffect(() => {
    setConversation(null);
    setContext(null);
    if (!projectId) {
      setConversations([]);
      setConversationId("");
      return;
    }
    loadConversations(projectId).catch((e) => setError(String(e)));
  }, [projectId, loadConversations]);

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

  async function registerProject(event: FormEvent) {
    event.preventDefault();
    if (!registerPath.trim()) return;
    setBusy(true); setError("");
    try {
      const project = newProjectName.trim()
        ? await api.createProject(registerPath.trim(), newProjectName.trim())
        : await api.registerProject(registerPath.trim());
      setRegisterPath(""); setNewProjectName("");
      await loadProjects();
      setProjectId(project.id);
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

  async function indexProject() {
    if (!projectId) return;
    setBusy(true); setError("");
    try {
      const result = await api.indexProject(projectId);
      alert(`Index complete\nAdded: ${result.added ?? 0}\nChanged: ${result.changed ?? 0}\nDeleted: ${result.deleted ?? 0}\nUnchanged: ${result.unchanged ?? 0}\nLocal-only (egress blocked): ${result.local_only ?? 0}`);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function proposalAction(proposal: Proposal, action: "approve-plan" | "reject") {
    if (!projectId) return;
    setBusy(true); setError("");
    try {
      if (action === "approve-plan") await api.approvePlan(projectId, proposal.id);
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
        <div className="brand">Project Assistant <span className="small">v0.4</span></div>

        <div className="section-title">Projects</div>
        {projects.map((project) => (
          <button key={project.id} className={`nav-item ${project.id === projectId ? "active" : ""}`} onClick={() => setProjectId(project.id)} title={project.path}>
            {project.name}
          </button>
        ))}

        <form className="form-stack" onSubmit={registerProject} style={{ marginTop: 10 }}>
          <input className="input" value={registerPath} onChange={(e) => setRegisterPath(e.target.value)} placeholder="Local project path" />
          <input className="input" value={newProjectName} onChange={(e) => setNewProjectName(e.target.value)} placeholder="Name (only for new project)" />
          <button className="btn" disabled={busy || !registerPath.trim()}>{newProjectName.trim() ? "Create project" : "Register existing"}</button>
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
                  <div className="mode">
                    <button className={mode === "chat" ? "active" : ""} onClick={() => setMode("chat")}>Chat</button>
                    <button className={mode === "propose" ? "active" : ""} onClick={() => setMode("propose")}>Propose change</button>
                  </div>
                  <button className="btn primary" onClick={() => void send()} disabled={busy || !message.trim()}>{busy ? "Working…" : mode === "propose" ? "Write proposal" : "Send"}</button>
                </div>
              </div>
            </div>}
          </>
        ) : (
          <ProjectPanel project={currentProject} busy={busy} sourcePath={sourcePath} sourceName={sourceName} setSourcePath={setSourcePath} setSourceName={setSourceName} addSource={addSource} indexProject={indexProject} />
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
  return <div className={`message ${role}`}>
    <div className="message-label">{entry.title}{entry.timestamp ? ` · ${timeLabel(entry.timestamp)}` : ""}</div>
    <div className="message-body">{entry.body}</div>
  </div>;
}

function ProjectPanel(props: {
  project: Project | null;
  busy: boolean;
  sourcePath: string;
  sourceName: string;
  setSourcePath: (value: string) => void;
  setSourceName: (value: string) => void;
  addSource: (event: FormEvent) => void;
  indexProject: () => void;
}) {
  const { project } = props;
  if (!project) return <div className="empty">Select a project.</div>;
  return <section className="chat-scroll">
    <h2 style={{ marginTop: 0 }}>Project</h2>
    <div className="card"><h3>Project directory</h3><div className="path">{project.path}</div></div>
    <div className="row" style={{ justifyContent: "space-between", marginTop: 24 }}><h3>Source repositories</h3><button className="btn" onClick={props.indexProject} disabled={props.busy}>Reindex changed files</button></div>
    <div className="source-list">
      {project.sources.length === 0 && <div className="notice">No external source repos registered yet.</div>}
      {project.sources.map((source) => <div className="source-row" key={source.name}><div className="source-name">{source.name}</div><div className="path">{source.path}</div></div>)}
    </div>
    <form onSubmit={props.addSource} className="form-stack" style={{ marginTop: 18, maxWidth: 600 }}>
      <input className="input" value={props.sourcePath} onChange={(e) => props.setSourcePath(e.target.value)} placeholder="/absolute/path/to/repo" />
      <input className="input" value={props.sourceName} onChange={(e) => props.setSourceName(e.target.value)} placeholder="Optional repo name" />
      <button className="btn primary" disabled={props.busy || !props.sourcePath.trim()}>Add source repo</button>
    </form>
  </section>;
}

function ContextPanel({ context }: { context: ContextSummary | null }) {
  if (!context) return <div className="notice">Context used by the most recent chat will appear here, or compile one manually.</div>;
  return <>
    <div className="card" style={{ marginTop: 12 }}>
      <h3>{context.estimated_tokens.toLocaleString("en-AU")} estimated tokens</h3>
      <div className="small">Routed repositories</div>
      <div className="row wrap" style={{ marginTop: 7 }}>{context.routed_repos.map((repo) => <span className="badge" key={repo}>{repo}</span>)}</div>
    </div>
    <div className="card"><h3>Retrieved source</h3><ul className="context-list">{context.rag_sources.slice(0, 12).map((source) => <li key={source}>{source}</li>)}</ul></div>
    {context.graph_sources.length > 0 && <div className="card"><h3>Graph sources</h3><ul className="context-list">{context.graph_sources.slice(0, 10).map((source) => <li key={source}>{source}</li>)}</ul></div>}
    {context.text && <div className="card"><h3>Compiled context</h3><div className="context-text">{context.text}</div>{context.debug_path && <div className="path" style={{ marginTop: 8 }}>{context.debug_path}</div>}</div>}
  </>;
}

function ProposalCard({ proposal, projectId, busy, onAction, onApplied }: { proposal: Proposal; projectId: string; busy: boolean; onAction: (proposal: Proposal, action: "approve-plan" | "reject") => void; onApplied: () => Promise<void> }) {
  const [patchPath, setPatchPath] = useState("");
  const [repoPath, setRepoPath] = useState(proposal.repo_path ?? "");
  const [localError, setLocalError] = useState("");
  const [working, setWorking] = useState(false);

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

    {proposal.status === "pending" && <div className="row" style={{ marginTop: 10 }}>
      <button className="btn primary" disabled={busy || working} onClick={() => onAction(proposal, "approve-plan")}>Approve plan</button>
      <button className="btn danger" disabled={busy || working} onClick={() => onAction(proposal, "reject")}>Reject</button>
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

    {proposal.status === "patch_pending" && <div className="row" style={{ marginTop: 10 }}>
      <button className="btn primary" disabled={busy || working} onClick={() => void run(() => api.approvePatch(projectId, proposal.id))}>Approve exact diff</button>
      <button className="btn danger" disabled={busy || working} onClick={() => onAction(proposal, "reject")}>Reject</button>
    </div>}

    {proposal.status === "patch_approved" && <div className="form-stack" style={{ marginTop: 10 }}>
      <div className="small">Only the hashed diff shown above can be applied. The backend rechecks the hash, repo, HEAD commit and <code>git apply --check</code> immediately before mutation.</div>
      <button className="btn primary" disabled={busy || working} onClick={() => void run(() => api.applyPatch(projectId, proposal.id))}>{working ? "Applying…" : "Apply approved diff"}</button>
    </div>}

    {localError && <div className="error" style={{ marginTop: 8 }}>{localError}</div>}
  </div>;
}

