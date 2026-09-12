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
import type { ChatSession, DocumentSummary, DocumentTemplate, FileAsset } from "@/lib/types";

interface WorkspaceState {
  sessions: ChatSession[];
  documents: DocumentSummary[];
  files: FileAsset[];
  templates: DocumentTemplate[];
  refreshSessions: () => Promise<void>;
  refreshDocuments: () => Promise<void>;
  refreshFiles: () => Promise<void>;
  refreshTemplates: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

/** One place for the collections the sidebar and several pages both need. */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [files, setFiles] = useState<FileAsset[]>([]);
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);

  const refreshSessions = useCallback(async () => {
    setSessions(await api.listSessions().catch(() => []));
  }, []);
  const refreshDocuments = useCallback(async () => {
    setDocuments(await api.listDocuments().catch(() => []));
  }, []);
  const refreshFiles = useCallback(async () => {
    setFiles(await api.listFiles().catch(() => []));
  }, []);
  const refreshTemplates = useCallback(async () => {
    setTemplates(await api.listTemplates().catch(() => []));
  }, []);

  useEffect(() => {
    if (!user) return;
    void refreshSessions();
    void refreshDocuments();
    void refreshFiles();
    void refreshTemplates();
  }, [user, refreshSessions, refreshDocuments, refreshFiles, refreshTemplates]);

  const value = useMemo(
    () => ({
      sessions,
      documents,
      files,
      templates,
      refreshSessions,
      refreshDocuments,
      refreshFiles,
      refreshTemplates,
    }),
    [
      sessions,
      documents,
      files,
      templates,
      refreshSessions,
      refreshDocuments,
      refreshFiles,
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
