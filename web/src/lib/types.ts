export type Source = { name: string; path: string };

export type Project = {
  id: string;
  name: string;
  path: string;
  sources: Source[];
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
  text?: string;
  debug_path?: string;
};
