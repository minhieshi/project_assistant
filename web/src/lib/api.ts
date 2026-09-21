import type {
  ContextSummary,
  ConversationDetail,
  ConversationSummary,
  Project,
  ProjectConversion,
  Proposal,
  AppStatus,
  IndexStatus,
} from "./types";

const API_BASE = "/api/backend";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail ?? message;
    } catch {}
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<AppStatus>("/status"),
  projects: () => request<Project[]>("/projects"),
  importProject: (path: string, name?: string) => request<Project>("/projects/import", { method: "POST", body: JSON.stringify({ path, name: name || null }) }),
  registerProject: (path: string) => request<Project>("/projects/register", { method: "POST", body: JSON.stringify({ path }) }),
  createProject: (name: string) => request<Project>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  renameProject: (id: string, name: string) => request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  forgetProject: (id: string) => request<{ ok: boolean }>(`/projects/${id}`, { method: "DELETE" }),
  convertProjectToSource: (id: string, targetProjectId: string, sourceName?: string) => request<ProjectConversion>(`/projects/${id}/convert-to-source`, { method: "POST", body: JSON.stringify({ target_project_id: targetProjectId, source_name: sourceName || null }) }),
  addSource: (id: string, path: string, name?: string) => request<Project>(`/projects/${id}/sources`, { method: "POST", body: JSON.stringify({ path, name: name || null }) }),
  removeSource: (id: string, name: string) => request<Project>(`/projects/${id}/sources/${encodeURIComponent(name)}`, { method: "DELETE" }),
  indexProject: (id: string) => request<Record<string, number>>(`/projects/${id}/index`, { method: "POST" }),
  indexStatus: (id: string) => request<IndexStatus>(`/projects/${id}/index-status`),
  conversations: (id: string) => request<ConversationSummary[]>(`/projects/${id}/conversations`),
  createConversation: (id: string, title: string) => request<ConversationSummary>(`/projects/${id}/conversations`, { method: "POST", body: JSON.stringify({ title }) }),
  conversation: (projectId: string, conversationId: string) => request<ConversationDetail>(`/projects/${projectId}/conversations/${conversationId}`),
  proposals: (projectId: string, conversationId?: string) => request<Proposal[]>(`/projects/${projectId}/proposals${conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ""}`),
  propose: (projectId: string, conversationId: string, changeRequest: string) => request<Proposal>(`/projects/${projectId}/conversations/${conversationId}/proposals`, { method: "POST", body: JSON.stringify({ request: changeRequest }) }),
  approvePlan: (projectId: string, proposalId: string, approvedActions: string[] = []) => request<Proposal>(`/projects/${projectId}/proposals/${proposalId}/approve-plan`, { method: "POST", body: JSON.stringify({ approved_actions: approvedActions }) }),
  reject: (projectId: string, proposalId: string) => request<Proposal>(`/projects/${projectId}/proposals/${proposalId}/reject`, { method: "POST" }),
  stagePatch: (projectId: string, proposalId: string, patchPath: string, repoPath: string) => request<Proposal>(`/projects/${projectId}/proposals/${proposalId}/stage-patch`, { method: "POST", body: JSON.stringify({ patch_path: patchPath, repo_path: repoPath }) }),
  approvePatch: (projectId: string, proposalId: string, approvalChecks: string[] = []) => request<Proposal>(`/projects/${projectId}/proposals/${proposalId}/approve-patch`, { method: "POST", body: JSON.stringify({ approval_checks: approvalChecks }) }),
  applyPatch: (projectId: string, proposalId: string) => request<Proposal>(`/projects/${projectId}/proposals/${proposalId}/apply`, { method: "POST" }),
  inspectContext: (projectId: string, query: string, conversationId?: string) => request<ContextSummary>(`/projects/${projectId}/context`, { method: "POST", body: JSON.stringify({ query, conversation_id: conversationId ?? null }) }),
};



export type StreamCallbacks = {
  onContext?: (context: ContextSummary) => void;
  onDelta?: (text: string) => void;
  onDone?: () => void;
};

export async function streamChat(projectId: string, conversationId: string, message: string, callbacks: StreamCallbacks) {
  const response = await fetch(`${API_BASE}/projects/${projectId}/conversations/${conversationId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    cache: "no-store",
  });
  if (!response.ok || !response.body) {
    let detail = `Chat stream failed: ${response.status} ${response.statusText}`;
    try { const body = await response.json(); detail = body.detail ?? detail; } catch {}
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "context") callbacks.onContext?.(payload as ContextSummary);
      if (event === "delta") callbacks.onDelta?.(payload.text ?? "");
      if (event === "done") callbacks.onDone?.();
      if (event === "error") throw new Error(payload.message ?? "Unknown stream error");
    }
  }
}
