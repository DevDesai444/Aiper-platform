"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ChatSession, DocumentSummary, DocumentTemplate, Project } from "@/lib/types";

interface WorkspaceState {
  projects: Project[];
  sessions: ChatSession[];
  documents: DocumentSummary[];
  templates: DocumentTemplate[];
  refreshProjects: () => Promise<void>;
  refreshSessions: () => Promise<void>;
  refreshDocuments: () => Promise<void>;
  refreshTemplates: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

/**
 * One place for the collections the sidebar and several pages both need.
 *
 * Every list here is already filtered by the access resolver on the server, so
 * what arrives is exactly what the caller may see — the UI never filters again.
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);

  const refreshProjects = useCallback(async () => {
    setProjects(await api.listProjects().catch(() => []));
  }, []);
  const refreshSessions = useCallback(async () => {
    setSessions(await api.listSessions().catch(() => []));
  }, []);
  const refreshDocuments = useCallback(async () => {
    setDocuments(await api.listDocuments().catch(() => []));
  }, []);
  const refreshTemplates = useCallback(async () => {
    setTemplates(await api.listTemplates().catch(() => []));
  }, []);

  useEffect(() => {
    if (!user) return;
    void refreshProjects();
    void refreshSessions();
    void refreshDocuments();
    void refreshTemplates();
  }, [user, refreshProjects, refreshSessions, refreshDocuments, refreshTemplates]);

  const value = useMemo(
    () => ({
      projects,
      sessions,
      documents,
      templates,
      refreshProjects,
      refreshSessions,
      refreshDocuments,
      refreshTemplates,
    }),
    [
      projects,
      sessions,
      documents,
      templates,
      refreshProjects,
      refreshSessions,
      refreshDocuments,
      refreshTemplates,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  return context;
}
