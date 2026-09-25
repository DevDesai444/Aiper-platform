import { getSupabaseAccessToken, getSupabaseClient, isSupabaseEnabled } from "@/lib/supabase";
import { chooseBearerToken } from "@/lib/supabase-helpers";
import type {
  AuthResponse,
  ChatSession,
  ChatSessionDetail,
  Collaborator,
  Diff,
  DocumentDetail,
  DocumentSummary,
  DocumentTemplate,
  Folder,
  Health,
  Mode,
  Project,
  ProjectTree,
  TemplateSection,
  User,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const TOKEN_KEY = "aiper.token";

/** The legacy self-issued token, held in localStorage. Unused on the Supabase path. */
export function getToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

/**
 * The Bearer token for outgoing requests: the Supabase access token when Supabase
 * owns identity, otherwise the legacy token. Async because fetching a fresh (and
 * possibly refreshed) Supabase session is async.
 */
export async function resolveBearerToken(): Promise<string | null> {
  const supabaseConfigured = isSupabaseEnabled();
  return chooseBearerToken({
    supabaseConfigured,
    supabaseAccessToken: supabaseConfigured ? await getSupabaseAccessToken() : null,
    legacyToken: supabaseConfigured ? null : getToken(),
  });
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** A 401 anywhere clears the session and sends the user back to /login. */
function onUnauthorised() {
  setToken(null);
  // On the Supabase path, also drop the (now-rejected) session so the app doesn't
  // keep retrying with a dead token. Fire-and-forget; the auth listener reacts.
  if (isSupabaseEnabled()) void getSupabaseClient()?.auth.signOut();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await resolveBearerToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (response.status === 401) {
    onUnauthorised();
    throw new ApiError(401, "Your session has expired. Please sign in again.");
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new ApiError(response.status, message);
  }
  // A 204/205 has no body by definition; an empty 200 is treated the same way.
  if (response.status === 204 || response.status === 205) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  health: () => request<Health>("/health"),

  /* auth */
  register: (body: {
    email: string;
    password: string;
    full_name?: string;
    organisation?: string;
  }) => request<AuthResponse>("/api/v1/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body: { email: string; password: string }) =>
    request<AuthResponse>("/api/v1/auth/login", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<User>("/api/v1/auth/me"),

  /* projects, folders and the tree */
  listProjects: () => request<Project[]>("/api/v1/projects"),
  createProject: (body: { name: string; description?: string }) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify(body) }),
  getProjectTree: (id: string) => request<ProjectTree>(`/api/v1/projects/${id}/tree`),
  createFolder: (projectId: string, body: { name: string; parent_folder_id?: string | null }) =>
    request<Folder>(`/api/v1/projects/${projectId}/folders`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /* templates */
  listTemplates: () => request<DocumentTemplate[]>("/api/v1/templates"),
  createTemplate: (body: {
    name: string;
    standard: string;
    description: string;
    sections: TemplateSection[];
  }) => request<DocumentTemplate>("/api/v1/templates", { method: "POST", body: JSON.stringify(body) }),
  deleteTemplate: (id: string) => request<void>(`/api/v1/templates/${id}`, { method: "DELETE" }),

  /* chat */
  listSessions: () => request<ChatSession[]>("/api/v1/chat/sessions"),
  getSession: (id: string) => request<ChatSessionDetail>(`/api/v1/chat/sessions/${id}`),
  deleteSession: (id: string) => request<void>(`/api/v1/chat/sessions/${id}`, { method: "DELETE" }),

  /* documents */
  listDocuments: () => request<DocumentSummary[]>("/api/v1/documents"),
  createDocument: (body: { title: string; project_id: string; folder_id?: string | null }) =>
    request<DocumentDetail>("/api/v1/documents", { method: "POST", body: JSON.stringify(body) }),
  createFromMarkdown: (body: {
    title: string;
    markdown: string;
    commit_message?: string;
    /** Omitted means the caller's own Workspace project, created on first use. */
    project_id?: string | null;
    folder_id?: string | null;
  }) =>
    request<DocumentDetail>("/api/v1/documents/from-markdown", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getDocument: (id: string) => request<DocumentDetail>(`/api/v1/documents/${id}`),
  renameDocument: (id: string, title: string) =>
    request<DocumentSummary>(`/api/v1/documents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteDocument: (id: string) => request<void>(`/api/v1/documents/${id}`, { method: "DELETE" }),
  commit: (id: string, body: { content_json: unknown; commit_message: string }) =>
    request<DocumentDetail>(`/api/v1/documents/${id}/commits`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getDiff: (documentId: string, revisionId: string) =>
    request<Diff>(`/api/v1/documents/${documentId}/revisions/${revisionId}/diff`),
  restore: (documentId: string, revisionId: string) =>
    request<DocumentDetail>(`/api/v1/documents/${documentId}/revisions/${revisionId}/restore`, {
      method: "POST",
    }),
  addCollaborator: (id: string, body: { email: string; role: "editor" | "viewer" }) =>
    request<Collaborator>(`/api/v1/documents/${id}/collaborators`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  removeCollaborator: (documentId: string, collaboratorId: string) =>
    request<void>(`/api/v1/documents/${documentId}/collaborators/${collaboratorId}`, {
      method: "DELETE",
    }),
};

export interface StreamRequest {
  message: string;
  mode: Mode;
  session_id?: string | null;
  /** Scopes a new conversation to a project; ignored for an existing session. */
  project_id?: string | null;
  template_key?: string | null;
}

/**
 * Native SSE parsing over fetch + ReadableStream. `EventSource` cannot send an
 * Authorization header, which rules it out here.
 */
export async function streamChat(
  body: StreamRequest,
  onEvent: (event: unknown) => void,
  signal?: AbortSignal,
) {
  const token = await resolveBearerToken();
  const response = await fetch(`${API_URL}/api/v1/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (response.status === 401) {
    onUnauthorised();
    throw new ApiError(401, "Your session has expired. Please sign in again.");
  }
  if (!response.ok || !response.body) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : message;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(response.status, message);
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
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch {
        /* ignore a partial frame */
      }
    }
  }
}
