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

/** One access_grants row for a project, with the user's display info joined in. */
export interface ProjectMember {
  user_id: string;
  email: string;
  full_name: string;
  role: AccessRole;
  created_at: string;
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

/** One row of a project's or document's audit trail — not to be confused
 * with `ActivityRow`, the chat agent's reasoning-trace feed. */
export interface AuditEntry {
  id: number;
  action: string;
  actor_id: string | null;
  actor_name: string | null;
  actor_email: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface Health {
  status: string;
  service: string;
  mock: boolean;
  azure_configured: boolean;
}

/* ── Comments (E6) ───────────────────────────────────────────────────── */

/** One comment row. A thread is the set of rows sharing (documentId, markId). */
export interface DocumentComment {
  id: string;
  document_id: string;
  mark_id: string;
  body: string;
  quoted_text: string;
  author_id: string | null;
  author_email: string;
  author_name: string;
  resolved_at: string | null;
  created_at: string;
}

/* ── Product tree (E9 P1) ─────────────────────────────────────────── */

export type NodeKind = "assembly" | "subassembly" | "component" | "part";
export type InterfaceKind = "power" | "data" | "rf" | "thermal" | "mechanical" | "fluid";
export type ParameterRole = "supply" | "accept" | "bidirectional";
export type NodeDocRelation = "reference" | "design-spec" | "test-report" | "sign-off" | "requirement";

export interface ProductNode {
  id: string;
  org_id: string;
  project_id: string;
  parent_node_id: string | null;
  kind: NodeKind;
  name: string;
  part_number: string | null;
  supplier: string;
  description: string;
  attributes: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface NodeTree {
  nodes: ProductNode[];
}

export interface NodeInterface {
  id: string;
  org_id: string;
  project_id: string;
  node_id: string;
  kind: InterfaceKind;
  name: string;
  description: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface InterfaceMate {
  id: string;
  org_id: string;
  project_id: string;
  interface_a_id: string;
  interface_b_id: string;
  note: string;
  created_by: string | null;
  created_at: string;
}

export interface NodeParameter {
  id: string;
  org_id: string;
  project_id: string;
  node_id: string;
  interface_id: string | null;
  role: ParameterRole;
  raw_name: string;
  normalized_name: string;
  definition_id: string | null;
  value_kind: "quantity" | "text";
  value_num: number | null;
  unit: string;
  tolerance_num: number | null;
  min_num: number | null;
  max_num: number | null;
  value_text: string;
  source_document_id: string | null;
  source_revision_number: number | null;
  source_quote: string;
  note: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface NodeDocumentLink {
  id: string;
  org_id: string;
  project_id: string;
  node_id: string;
  document_id: string;
  relation: NodeDocRelation;
  created_by: string | null;
  created_at: string;
}

export interface ParameterDefinition {
  id: string;
  org_id: string;
  key: string;
  display_name: string;
  dimension: string;
  canonical_unit: string;
  criticality: "standard" | "critical";
  description: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}
