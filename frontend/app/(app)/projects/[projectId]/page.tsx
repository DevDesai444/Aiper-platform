"use client";

import {
  ChevronRight,
  Cpu,
  FilePlus,
  Files,
  FolderPlus,
  History,
  MessagesSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  Users,
} from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { DocumentRow } from "@/components/projects/document-row";
import { ProductTreeTab } from "@/components/projects/product-tree-tab";
import { FolderCard } from "@/components/projects/folder-card";
import type { MenuAction } from "@/components/projects/item-menu";
import { MoveDialog } from "@/components/projects/move-dialog";
import { NameDialog } from "@/components/projects/name-dialog";
import { ProjectShareDialog } from "@/components/projects/project-share-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { ShareDialog } from "@/components/editor/share-dialog";
import { openActivityLog } from "@/lib/audit-log";
import { ApiError, api } from "@/lib/api";
import { extractRef, idSlug } from "@/lib/slug";
import type { DocumentDetail, Folder, ProjectTree, TreeDocument } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

type DialogState =
  | { kind: "new-folder" }
  | { kind: "new-document" }
  | { kind: "rename-project" }
  | { kind: "rename-folder"; folder: Folder }
  | { kind: "rename-document"; document: TreeDocument }
  | { kind: "move-folder"; folder: Folder }
  | { kind: "move-document"; document: TreeDocument }
  | { kind: "delete-project" }
  | { kind: "delete-folder"; folder: Folder }
  | { kind: "delete-document"; document: TreeDocument }
  | null;

/** The chain of folders from the project root down to `folderId`, inclusive. */
function ancestry(folders: Folder[], folderId: string | null): Folder[] {
  const byId = new Map(folders.map((folder) => [folder.id, folder]));
  const chain: Folder[] = [];
  let current = folderId ? byId.get(folderId) : undefined;
  // Bounded: a cycle in parent_folder_id must not hang the breadcrumb.
  for (let depth = 0; current && depth < 50; depth += 1) {
    chain.unshift(current);
    current = current.parent_folder_id ? byId.get(current.parent_folder_id) : undefined;
  }
  return chain;
}

export default function ProjectPage() {
  const { projectId: rawParam } = useParams<{ projectId: string }>();
  const projectId = extractRef(rawParam);
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentFolderId = searchParams.get("folder");
  const activeTab = searchParams.get("tab") ?? "documents";
  const { refreshDocuments } = useWorkspace();

  const [tree, setTree] = useState<ProjectTree | null>(null);
  const [missing, setMissing] = useState(false);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [busy, setBusy] = useState(false);
  // The row only carries a TreeDocument (no collaborators); Share needs the
  // full DocumentDetail, fetched on demand rather than kept in the tree.
  const [shareTarget, setShareTarget] = useState<DocumentDetail | null>(null);
  const [shareOpen, setShareOpen] = useState(false);

  async function openShare(document: TreeDocument) {
    try {
      setShareTarget(await api.getDocument(document.id));
    } catch (error) {
      toast.error("Could not open sharing", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  const load = useCallback(async () => {
    if (!projectId) {
      setMissing(true);
      return;
    }
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

  // Canonical polish: once the name is known, fix up a stale or missing slug
  // in place. A replace, not a push — no history entry, no navigation flash.
  useEffect(() => {
    if (!projectId || !tree) return;
    const canonical = idSlug(tree.project?.name ?? "", projectId);
    if (rawParam !== canonical) {
      const query = searchParams.toString();
      router.replace(`/projects/${canonical}${query ? `?${query}` : ""}`);
    }
  }, [projectId, rawParam, tree, router, searchParams]);

  const canEdit = tree?.project?.access === "owner" || tree?.project?.access === "editor";
  const isOwner = tree?.project?.access === "owner";
  const projectHref = projectId
    ? `/projects/${idSlug(tree?.project?.name ?? "", projectId)}`
    : "/projects";
  const hrefForFolder = useCallback(
    (folderId: string | null) => (folderId ? `${projectHref}?folder=${folderId}` : projectHref),
    [projectHref],
  );

  function tabHref(tab: string) {
    return tab === "documents" ? projectHref : `${projectHref}?tab=${tab}`;
  }

  const crumbs = useMemo(
    () => (tree ? ancestry(tree.folders, currentFolderId) : []),
    [tree, currentFolderId],
  );
  const currentFolder = crumbs.at(-1) ?? null;

  const childFolders = useMemo(
    () => (tree ? tree.folders.filter((f) => f.parent_folder_id === currentFolderId) : []),
    [tree, currentFolderId],
  );
  const childDocuments = useMemo(
    () => (tree ? tree.documents.filter((d) => d.folder_id === currentFolderId) : []),
    [tree, currentFolderId],
  );

  async function createFolder(name: string) {
    if (!projectId) return;
    setBusy(true);
    try {
      await api.createFolder(projectId, { name, parent_folder_id: currentFolderId });
      await load();
      setDialog(null);
    } catch (error) {
      toast.error("Could not create the folder", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function createDocument(title: string) {
    if (!projectId) return;
    setBusy(true);
    try {
      const document = await api.createDocument({
        title,
        project_id: projectId,
        folder_id: currentFolderId,
      });
      await Promise.all([refreshDocuments(), load()]);
      setDialog(null);
      router.push(`${projectHref}/d/${idSlug(document.title, document.id)}`);
    } catch (error) {
      toast.error("Could not create the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function renameProject(name: string) {
    if (!projectId) return;
    setBusy(true);
    try {
      await api.renameProject(projectId, name);
      await load();
      setDialog(null);
    } catch (error) {
      toast.error("Could not rename the project", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function renameFolder(folder: Folder, name: string) {
    if (!projectId) return;
    setBusy(true);
    try {
      await api.renameFolder(projectId, folder.id, name);
      await load();
      setDialog(null);
    } catch (error) {
      toast.error("Could not rename the folder", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function renameDocumentRow(document: TreeDocument, title: string) {
    setBusy(true);
    try {
      await api.renameDocument(document.id, title);
      await Promise.all([refreshDocuments(), load()]);
      setDialog(null);
    } catch (error) {
      toast.error("Could not rename the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function moveFolder(folder: Folder, destination: string | null) {
    if (!projectId) return;
    setBusy(true);
    try {
      await api.moveFolder(projectId, folder.id, destination);
      await load();
      setDialog(null);
    } catch (error) {
      toast.error("Could not move the folder", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function moveDocumentRow(document: TreeDocument, destination: string | null) {
    setBusy(true);
    try {
      await api.moveDocument(document.id, destination);
      await Promise.all([refreshDocuments(), load()]);
      setDialog(null);
    } catch (error) {
      toast.error("Could not move the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject() {
    if (!projectId) return;
    setBusy(true);
    try {
      await api.deleteProject(projectId);
      toast.success(`Deleted ${tree?.project?.name ?? "the project"}`);
      await refreshDocuments();
      router.push("/projects");
    } catch (error) {
      toast.error("Could not delete the project", {
        description: error instanceof Error ? error.message : undefined,
      });
      setBusy(false);
    }
  }

  async function deleteFolder(folder: Folder) {
    if (!projectId) return;
    setBusy(true);
    try {
      await api.deleteFolder(projectId, folder.id);
      await Promise.all([refreshDocuments(), load()]);
      setDialog(null);
    } catch (error) {
      toast.error("Could not delete the folder", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function deleteDocumentRow(document: TreeDocument) {
    setBusy(true);
    try {
      await api.deleteDocument(document.id);
      await Promise.all([refreshDocuments(), load()]);
      setDialog(null);
    } catch (error) {
      toast.error("Could not delete the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  function folderActions(folder: Folder): MenuAction[] {
    const actions: MenuAction[] = [
      {
        key: "open",
        label: "Open",
        icon: FolderPlus,
        onSelect: () => router.push(hrefForFolder(folder.id)),
      },
    ];
    if (canEdit) {
      actions.push(
        {
          key: "rename",
          label: "Rename",
          icon: Pencil,
          onSelect: () => setDialog({ kind: "rename-folder", folder }),
        },
        {
          key: "move",
          label: "Move to…",
          icon: FolderPlus,
          onSelect: () => setDialog({ kind: "move-folder", folder }),
        },
      );
    }
    if (isOwner) {
      actions.push({
        key: "delete",
        label: "Delete",
        icon: Trash2,
        destructive: true,
        onSelect: () => setDialog({ kind: "delete-folder", folder }),
      });
    }
    return actions;
  }

  function documentActions(document: TreeDocument): MenuAction[] {
    const actions: MenuAction[] = [
      {
        key: "open",
        label: "Open",
        icon: FilePlus,
        onSelect: () => router.push(`${projectHref}/d/${idSlug(document.title, document.id)}`),
      },
      {
        key: "share",
        label: "Share",
        icon: Users,
        onSelect: () => void openShare(document),
      },
      {
        key: "activity",
        label: "Activity",
        icon: History,
        onSelect: () => openActivityLog("document", idSlug(document.title, document.id)),
      },
    ];
    if (canEdit) {
      actions.push(
        {
          key: "rename",
          label: "Rename",
          icon: Pencil,
          onSelect: () => setDialog({ kind: "rename-document", document }),
        },
        {
          key: "move",
          label: "Move to…",
          icon: FolderPlus,
          onSelect: () => setDialog({ kind: "move-document", document }),
        },
      );
    }
    // Document delete is owner-only server-side too, but per-document — a
    // caller with editor-on-the-project can legitimately own some documents
    // and not others, so the check is left to the request itself rather than
    // gated here; a 403 on an occasional attempt is unusual enough to just report.
    actions.push({
      key: "delete",
      label: "Delete",
      icon: Trash2,
      destructive: true,
      onSelect: () => setDialog({ kind: "delete-document", document }),
    });
    return actions;
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
            <Button
              className="mt-4"
              size="sm"
              variant="outline"
              onClick={() => router.push("/projects")}
            >
              Back to projects
            </Button>
          </div>
        </div>
      </>
    );
  }

  if (!projectId || !tree) {
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

  const title = currentFolder?.name ?? tree.project?.name ?? "Shared with you";

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-1">
            {title}
            {tree.project && !currentFolder ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    aria-label="Project actions"
                    className="rounded p-0.5 text-muted-foreground opacity-70 transition-opacity hover:bg-accent hover:opacity-100"
                  >
                    <MoreHorizontal className="size-3.5" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  <DropdownMenuItem onSelect={() => setShareOpen(true)}>
                    <Users />
                    Share
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() =>
                      openActivityLog("project", idSlug(tree.project?.name ?? "", projectId ?? ""))
                    }
                  >
                    <History />
                    Activity
                  </DropdownMenuItem>
                  {isOwner ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={() => setDialog({ kind: "rename-project" })}>
                        <Pencil />
                        Rename project
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        destructive
                        onSelect={() => setDialog({ kind: "delete-project" })}
                      >
                        <Trash2 />
                        Delete project
                      </DropdownMenuItem>
                    </>
                  ) : null}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
          </span>
        }
        description={
          // A <p> is PageHeader's own wrapper for `description`, and <p> cannot
          // contain block-level content — role="navigation" on a <span> keeps
          // the breadcrumb landmark without the invalid <nav>-inside-<p> nesting.
          <span
            role="navigation"
            aria-label="Breadcrumb"
            className="flex items-center gap-1 overflow-x-auto"
          >
            <button
              onClick={() => router.push(projectHref)}
              className="shrink-0 transition-colors hover:text-foreground"
            >
              Projects
            </button>
            <ChevronRight className="size-3 shrink-0 opacity-50" />
            <button
              onClick={() => router.push(projectHref)}
              className="shrink-0 transition-colors hover:text-foreground"
            >
              {tree.project?.name ?? "this project"}
            </button>
            {crumbs.map((folder) => (
              <span key={folder.id} className="flex shrink-0 items-center gap-1">
                <ChevronRight className="size-3 opacity-50" />
                <button
                  onClick={() => router.push(hrefForFolder(folder.id))}
                  className="transition-colors hover:text-foreground"
                >
                  {folder.name}
                </button>
              </span>
            ))}
          </span>
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
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm">
                    <Plus />
                    New
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => setDialog({ kind: "new-document" })}>
                    <FilePlus />
                    Document
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setDialog({ kind: "new-folder" })}>
                    <FolderPlus />
                    Folder
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
          </>
        }
      />

      {/* Tab strip — only show when we can see the project root */}
      {tree.project && !currentFolderId ? (
        <div className="flex shrink-0 border-b border-border px-6">
          {(
            [
              { id: "documents", label: "Documents", Icon: Files },
              { id: "product-tree", label: "Product tree", Icon: Cpu },
            ] as const
          ).map(({ id, label, Icon }) => (
            <button
              key={id}
              onClick={() => router.push(tabHref(id))}
              className={[
                "flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-[0.8125rem] transition-colors",
                activeTab === id
                  ? "border-foreground font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              <Icon className="size-3.5" />
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {activeTab === "product-tree" && projectId ? (
        <ProductTreeTab projectId={projectId} canEdit={canEdit ?? false} />
      ) : (
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-4xl space-y-6">
              {childFolders.length === 0 && childDocuments.length === 0 ? (
                <div className="mx-auto max-w-md rounded-xl border border-dashed border-border bg-surface px-8 py-14 text-center">
                  <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                    Nothing here yet.
                    {canEdit ? " Right-click, or use New, to add a folder or a document." : null}
                  </p>
                </div>
              ) : null}

              {childFolders.length > 0 ? (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
                  {childFolders.map((folder) => (
                    <FolderCard
                      key={folder.id}
                      folder={folder}
                      onOpen={() => router.push(hrefForFolder(folder.id))}
                      actions={folderActions(folder)}
                    />
                  ))}
                </div>
              ) : null}

              {childDocuments.length > 0 ? (
                <div className="space-y-0.5">
                  {childDocuments.map((document) => (
                    <DocumentRow
                      key={document.id}
                      document={document}
                      onOpen={() =>
                        router.push(`${projectHref}/d/${idSlug(document.title, document.id)}`)
                      }
                      actions={documentActions(document)}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </ContextMenuTrigger>
        {canEdit ? (
          <ContextMenuContent>
            <ContextMenuItem onSelect={() => setDialog({ kind: "new-document" })}>
              <FilePlus className="size-3.5" />
              New document here
            </ContextMenuItem>
            <ContextMenuItem onSelect={() => setDialog({ kind: "new-folder" })}>
              <FolderPlus className="size-3.5" />
              New folder here
            </ContextMenuItem>
          </ContextMenuContent>
        ) : null}
      </ContextMenu>
      )}

      <NameDialog
        open={dialog?.kind === "new-folder"}
        onOpenChange={(open) => !open && setDialog(null)}
        title={currentFolder ? `New folder in ${currentFolder.name}` : "New folder"}
        label="Folder name"
        placeholder="Requirements"
        action="Create folder"
        pending={busy}
        onSubmit={(name) => void createFolder(name)}
      />

      <NameDialog
        open={dialog?.kind === "new-document"}
        onOpenChange={(open) => !open && setDialog(null)}
        title={currentFolder ? `New document in ${currentFolder.name}` : "New document"}
        label="Document title"
        placeholder="Mission system specification"
        initialValue="Untitled document"
        action="Create document"
        pending={busy}
        onSubmit={(title) => void createDocument(title)}
      />

      <NameDialog
        open={dialog?.kind === "rename-project"}
        onOpenChange={(open) => !open && setDialog(null)}
        title="Rename project"
        label="Project name"
        initialValue={tree.project?.name ?? ""}
        action="Rename"
        pending={busy}
        onSubmit={(name) => void renameProject(name)}
      />

      <NameDialog
        open={dialog?.kind === "rename-folder"}
        onOpenChange={(open) => !open && setDialog(null)}
        title="Rename folder"
        label="Folder name"
        initialValue={dialog?.kind === "rename-folder" ? dialog.folder.name : ""}
        action="Rename"
        pending={busy}
        onSubmit={(name) => dialog?.kind === "rename-folder" && void renameFolder(dialog.folder, name)}
      />

      <NameDialog
        open={dialog?.kind === "rename-document"}
        onOpenChange={(open) => !open && setDialog(null)}
        title="Rename document"
        label="Document title"
        initialValue={dialog?.kind === "rename-document" ? dialog.document.title : ""}
        action="Rename"
        pending={busy}
        onSubmit={(title) =>
          dialog?.kind === "rename-document" && void renameDocumentRow(dialog.document, title)
        }
      />

      <MoveDialog
        open={dialog?.kind === "move-folder"}
        onOpenChange={(open) => !open && setDialog(null)}
        folders={tree.folders}
        currentFolderId={dialog?.kind === "move-folder" ? dialog.folder.parent_folder_id : null}
        excludeFolderId={dialog?.kind === "move-folder" ? dialog.folder.id : null}
        itemLabel="folder"
        pending={busy}
        onSubmit={(destination) =>
          dialog?.kind === "move-folder" && void moveFolder(dialog.folder, destination)
        }
      />

      <MoveDialog
        open={dialog?.kind === "move-document"}
        onOpenChange={(open) => !open && setDialog(null)}
        folders={tree.folders}
        currentFolderId={dialog?.kind === "move-document" ? dialog.document.folder_id : null}
        itemLabel="document"
        pending={busy}
        onSubmit={(destination) =>
          dialog?.kind === "move-document" && void moveDocumentRow(dialog.document, destination)
        }
      />

      <ConfirmDialog
        open={dialog?.kind === "delete-project"}
        onOpenChange={(open) => !open && setDialog(null)}
        title={`Delete ${tree.project?.name ?? "this project"}?`}
        description="This deletes the project and every folder and document inside it, for everyone it is shared with. This cannot be undone."
        confirmLabel="Delete project"
        pending={busy}
        onConfirm={() => void deleteProject()}
      />

      <ConfirmDialog
        open={dialog?.kind === "delete-folder"}
        onOpenChange={(open) => !open && setDialog(null)}
        title={dialog?.kind === "delete-folder" ? `Delete ${dialog.folder.name}?` : ""}
        description="This deletes the folder and every subfolder inside it. Documents inside move to the project root — nothing is destroyed. This cannot be undone."
        confirmLabel="Delete folder"
        pending={busy}
        onConfirm={() => dialog?.kind === "delete-folder" && void deleteFolder(dialog.folder)}
      />

      <ConfirmDialog
        open={dialog?.kind === "delete-document"}
        onOpenChange={(open) => !open && setDialog(null)}
        title={dialog?.kind === "delete-document" ? `Delete ${dialog.document.title}?` : ""}
        description="This deletes the document and its entire revision history. This cannot be undone."
        confirmLabel="Delete document"
        pending={busy}
        onConfirm={() => dialog?.kind === "delete-document" && void deleteDocumentRow(dialog.document)}
      />

      {shareTarget ? (
        <ShareDialog
          document={shareTarget}
          canManage={shareTarget.access === "owner"}
          onChange={(collaborators) =>
            setShareTarget((current) => (current ? { ...current, collaborators } : current))
          }
          open={shareTarget !== null}
          onOpenChange={(open) => !open && setShareTarget(null)}
        />
      ) : null}

      {tree.project ? (
        <ProjectShareDialog
          project={tree.project}
          canManage={isOwner}
          open={shareOpen}
          onOpenChange={setShareOpen}
        />
      ) : null}
    </>
  );
}
