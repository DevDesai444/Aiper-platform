export type Mode = "document_generation" | "feature_comparison";

/* ── The tenancy tree: organisation → project → folder → document ────── */

/** The caller's own effective role, as the access resolver reports it. */
export type AccessRole = "owner" | "editor" | "viewer";

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  access: AccessRole;
}

export interface Folder {
  id: string;
  project_id: string;
  parent_folder_id: string | null;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface TreeDocument {
  id: string;
  title: string;
  folder_id: string | null;
  revision_count: number;
  updated_at: string;
}

/**
 * Only what the caller may see. `project` is null when they reach into this
 * project through a folder or document grant without being able to see the
 * project itself.
 */
export interface ProjectTree {
  project: Project | null;
  folders: Folder[];
  documents: TreeDocument[];
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  organisation: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface TemplateSection {
  number?: string;
  title: string;
  guidance?: string;
}

export interface DocumentTemplate {
  id: string;
  key: string;
  name: string;
  standard: string;
  description: string;
  sections: TemplateSection[];
  is_builtin: boolean;
}

/* ── The agent activity feed ─────────────────────────────────────────── */

export interface PlanItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

export type AgentEvent =
  | { type: "session"; session_id: string; title: string }
  | { type: "status"; value: "running" | "done" }
  | { type: "plan"; id: string; items: PlanItem[] }
  | { type: "skill"; id: string; skill: string }
  | { type: "tool_start"; id: string; tool: string; label: string; detail?: string; parent?: string }
  | { type: "tool_end"; id: string; tool: string; output?: string }
  | { type: "delegation"; id: string; agent: string; task?: string; status: string }
  | { type: "delegation_end"; id: string; output?: string }
  | { type: "token"; value: string }
  | { type: "final"; message_id: string; session_id: string; content: string }
  | { type: "error"; message: string };

/** One row in the rendered feed — tool_start/tool_end folded together. */
export interface ActivityRow {
  id: string;
  kind: "plan" | "skill" | "tool" | "delegation" | "error";
  label: string;
  agent?: string;
  detail?: string;
  output?: string;
  items?: PlanItem[];
  /** Tool calls a sub-agent made while handling this delegation. */
  children?: ActivityRow[];
  running: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode: Mode | null;
  activity: AgentEvent[];
  created_at: string;
}

export interface ChatSession {
  id: string;
  title: string;
  mode: Mode;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[];
}

/* ── Versioned documents ─────────────────────────────────────────────── */

export interface Revision {
  id: string;
  revision_number: number;
  parent_revision_id: string | null;
  commit_message: string;
  source: "human" | "agent";
  author_email: string;
  author_name: string;
  created_at: string;
  additions: number;
  deletions: number;
  modifications: number;
}

export interface Collaborator {
  id: string;
  email: string;
  role: "editor" | "viewer";
  invite_status: "pending" | "accepted";
  created_at: string;
}

export interface DocumentSummary {
  id: string;
  title: string;
  revision_count: number;
  created_at: string;
  updated_at: string;
  access: "owner" | "shared" | "editor" | "viewer";
  owner_email: string;
  project_id: string | null;
  folder_id: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  content_json: Record<string, unknown>;
  revisions: Revision[];
  collaborators: Collaborator[];
}

export interface DiffBlock {
  kind: "equal" | "add" | "remove" | "modify" | "collapsed";
  old_text?: string | null;
  new_text?: string | null;
  old_line?: number | null;
  new_line?: number | null;
  count?: number | null;
}

export interface Diff {
  additions: number;
  deletions: number;
  modifications: number;
  blocks: DiffBlock[];
}

export interface Health {
  status: string;
  service: string;
  mock: boolean;
  azure_configured: boolean;
}
