"use client";

import { FolderPlus, MessagesSquare, Plus } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { NameDialog } from "@/components/projects/name-dialog";
import { ProjectTreeView } from "@/components/projects/tree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api } from "@/lib/api";
import type { Folder, ProjectTree } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

type Pending =
  | { kind: "folder"; parent: Folder | null }
  | { kind: "document"; folder: Folder | null }
  | null;

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const { refreshDocuments } = useWorkspace();

  const [tree, setTree] = useState<ProjectTree | null>(null);
  const [missing, setMissing] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setTree(await api.getProjectTree(projectId));
    } catch (error) {
      // A project the caller cannot reach is reported as missing by the
      // resolver, and that is exactly how it is shown: never "no permission",
      // which would confirm it exists.
      if (error instanceof ApiError && error.status === 404) setMissing(true);
      else
        toast.error("Could not load the project", {
          description: error instanceof Error ? error.message : undefined,
        });
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const canEdit = tree?.project?.access === "owner" || tree?.project?.access === "editor";

  async function createFolder(name: string, parent: Folder | null) {
    setBusy(true);
    try {
      await api.createFolder(projectId, { name, parent_folder_id: parent?.id ?? null });
      await load();
      setPending(null);
    } catch (error) {
      toast.error("Could not create the folder", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function createDocument(title: string, folder: Folder | null) {
    setBusy(true);
    try {
      const document = await api.createDocument({
        title,
        project_id: projectId,
        folder_id: folder?.id ?? null,
      });
      await Promise.all([refreshDocuments(), load()]);
      setPending(null);
      router.push(`/projects/${projectId}/documents/${document.id}`);
    } catch (error) {
      toast.error("Could not create the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  if (missing) {
    return (
      <>
        <PageHeader title="Not found" />
        <div className="flex flex-1 items-center justify-center px-6">
          <div className="max-w-sm text-center">
            <p className="text-[0.8125rem] text-muted-foreground">
              This project does not exist, or there is nothing in it you can open.
            </p>
            <Button className="mt-4" size="sm" variant="outline" onClick={() => router.push("/projects")}>
              Back to projects
            </Button>
          </div>
        </div>
      </>
    );
  }

  if (!tree) {
    return (
      <>
        <PageHeader title={<Skeleton className="h-4 w-40" />} />
        <div className="space-y-2 px-6 py-6">
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-7 w-52" />
          <Skeleton className="h-7 w-72" />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={tree.project?.name ?? "Shared with you"}
        description={
          tree.project?.description ||
          (tree.project
            ? `${tree.folders.length} folder${tree.folders.length === 1 ? "" : "s"} · ${tree.documents.length} document${tree.documents.length === 1 ? "" : "s"}`
            : "The folders and documents you have been given access to")
        }
        actions={
          <>
            {tree.project ? (
              <Badge variant={tree.project.access === "owner" ? "default" : "brand"}>
                {tree.project.access}
              </Badge>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push(`/chat?project=${projectId}`)}
            >
              <MessagesSquare />
              Chat in project
            </Button>
            {canEdit ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPending({ kind: "folder", parent: null })}
                >
                  <FolderPlus />
                  New folder
                </Button>
                <Button size="sm" onClick={() => setPending({ kind: "document", folder: null })}>
                  <Plus />
                  New document
                </Button>
              </>
            ) : null}
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto max-w-3xl">
          <ProjectTreeView
            folders={tree.folders}
            documents={tree.documents}
            canEdit={Boolean(canEdit)}
            onOpen={(document) =>
              router.push(`/projects/${projectId}/documents/${document.id}`)
            }
            onNewFolder={(parent) => setPending({ kind: "folder", parent })}
            onNewDocument={(folder) => setPending({ kind: "document", folder })}
          />
        </div>
      </div>

      <NameDialog
        open={pending?.kind === "folder"}
        onOpenChange={(open) => !open && setPending(null)}
        title={
          pending?.kind === "folder" && pending.parent
            ? `New folder in ${pending.parent.name}`
            : "New folder"
        }
        label="Folder name"
        placeholder="Requirements"
        action="Create folder"
        pending={busy}
        onSubmit={(name) =>
          void createFolder(name, pending?.kind === "folder" ? pending.parent : null)
        }
      />

      <NameDialog
        open={pending?.kind === "document"}
        onOpenChange={(open) => !open && setPending(null)}
        title={
          pending?.kind === "document" && pending.folder
            ? `New document in ${pending.folder.name}`
            : "New document"
        }
        label="Document title"
        placeholder="Mission system specification"
        initialValue="Untitled document"
        action="Create document"
        pending={busy}
        onSubmit={(title) =>
          void createDocument(title, pending?.kind === "document" ? pending.folder : null)
        }
      />
    </>
  );
}
