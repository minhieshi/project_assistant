export type Source = { name: string; path: string };

export type Project = {
  id: string;
  name: string;
  path: string;
  kind: "managed" | "imported";
  sources: Source[];
};

export type ProjectConversion = {
  removed_project_id: string;
  target_project: Project;
  source: Source;
};

export type ConversationSummary = {
  id: string;
  title: string;
  path: string;
  updated_at?: string | null;
};

export type ConversationEntry = {
  title: string;
  timestamp: string;
  body: string;
  role: "user" | "assistant" | "system" | "event";
};

export type ConversationDetail = {
  id: string;
  title: string;
  path: string;
  markdown: string;
  entries: ConversationEntry[];
};

export type Proposal = {
  id: string;
  conversation_id: string;
  request: string;
  plan: string;
  status: "pending" | "plan_approved" | "patch_pending" | "patch_approved" | "rejected" | "applied";
  created_at: string;
  plan_approved_at?: string | null;
  repo_path?: string | null;
  patch_file?: string | null;
  patch_sha256?: string | null;
  base_commit?: string | null;
  patch_staged_at?: string | null;
  patch_approved_at?: string | null;
  applied_at?: string | null;
  patch_text?: string | null;
};

export type ContextSummary = {
  estimated_tokens: number;
  routed_repos: string[];
  rag_sources: string[];
  graph_sources: string[];
  retrieval_queries: string[];
  retrieval_actions: string[];
  retrieval_warnings?: string[];
  text?: string;
  debug_path?: string;
};

export type AppStatus = {
  version: string;
  projects_root: string;
  portkey: {
    base_url: string;
    base_url_configured: boolean;
    api_key_configured: boolean;
    chat_model: string;
    embedding_model_configured: boolean;
  };
};

export type RepoIndexStats = {
  scanned: number;
  eligible: number;
  indexed: number;
  unchanged: number;
  skipped: number;
  local_only: number;
  failed: number;
  chunks: number;
};

export type IndexStatus = {
  state: "idle" | "running" | "completed" | "failed";
  started_at?: string | null;
  finished_at?: string | null;
  current_repo?: string | null;
  current_file?: string | null;
  scanned: number;
  eligible: number;
  indexed: number;
  unchanged: number;
  skipped: number;
  local_only: number;
  failed: number;
  added: number;
  changed: number;
  deleted: number;
  chunks: number;
  skip_reasons: Record<string, number>;
  local_only_reasons: Record<string, number>;
  repos: Record<string, RepoIndexStats>;
  recent_skips: Array<{ repo: string; path: string; reason: string }>;
  recent_local_only: Array<{ repo: string; path: string; reason: string }>;
  last_error?: string | null;
};
