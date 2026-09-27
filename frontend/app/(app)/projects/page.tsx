"use client";

import { formatDistanceToNow } from "date-fns";
import { FolderKanban, FolderOpen, History, MessagesSquare, Pencil, Plus, Trash2, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { ItemMenu, type MenuAction } from "@/components/projects/item-menu";
import { NameDialog } from "@/components/projects/name-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { openActivityLog } from "@/lib/audit-log";
import { api } from "@/lib/api";
import { idSlug } from "@/lib/slug";
import type { Project } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

type DialogState =
  | { kind: "new-project" }
  | { kind: "rename-project"; project: Project }
  | { kind: "delete-project"; project: Project }
  | null;

export default function ProjectsPage() {
  const router = useRouter();
  const { projects, refreshProjects } = useWorkspace();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [busy, setBusy] = useState(false);

  function hrefFor(project: Project) {
    return `/projects/${idSlug(project.name, project.id)}`;
  }

  async function create(name: string) {
    setBusy(true);
    try {
      const project = await api.createProject({ name });
      await refreshProjects();
      setDialog(null);
      router.push(hrefFor(project));
    } catch (error) {
      toast.error("Could not create the project", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function rename(project: Project, name: string) {
    setBusy(true);
    try {
      await api.renameProject(project.id, name);
      await refreshProjects();
      setDialog(null);
    } catch (error) {
      toast.error("Could not rename the project", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function remove(project: Project) {
    setBusy(true);
    try {
      await api.deleteProject(project.id);
      toast.success(`Deleted ${project.name}`);
      await refreshProjects();
      setDialog(null);
    } catch (error) {
      toast.error("Could not delete the project", {
        description: error instanceof Error ? error.message : undefined,
      });
      setBusy(false);
    }
  }

  function actionsFor(project: Project): MenuAction[] {
    const actions: MenuAction[] = [
      {
        key: "open",
        label: "Open",
        icon: FolderOpen,
        onSelect: () => router.push(hrefFor(project)),
      },
      {
        key: "open-in-chat",
        label: "Open in chat",
        icon: MessagesSquare,
        onSelect: () => router.push(`/chat?project=${project.id}`),
      },
      {
        key: "activity",
        label: "Activity",
        icon: History,
        onSelect: () => openActivityLog("project", idSlug(project.name, project.id)),
      },
    ];
    if (project.access === "owner" || project.access === "editor") {
      actions.push({
        key: "rename",
        label: "Rename",
        icon: Pencil,
        onSelect: () => setDialog({ kind: "rename-project", project }),
      });
    }
    if (project.access === "owner") {
      actions.push({
        key: "delete",
        label: "Delete",
        icon: Trash2,
        destructive: true,
        onSelect: () => setDialog({ kind: "delete-project", project }),
      });
    }
    return actions;
  }

  return (
    <>
      <PageHeader
        title="Projects"
        description="Everything you work on lives in a project"
        actions={
          <Button size="sm" onClick={() => setDialog({ kind: "new-project" })}>
            <Plus />
            New project
          </Button>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {projects.length === 0 ? (
          <div className="mx-auto max-w-md rounded-xl border border-dashed border-border bg-surface px-8 py-14 text-center">
            <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border bg-surface-sunken">
              <FolderKanban className="size-4 text-muted-foreground" />
            </div>
            <h2 className="mt-4 text-[0.9375rem] font-semibold">No projects yet</h2>
            <p className="mx-auto mt-1.5 max-w-xs text-balance text-[0.8125rem] leading-relaxed text-muted-foreground">
              A project holds folders and documents, and is the unit you share with your team.
            </p>
            <Button className="mt-5" size="sm" onClick={() => setDialog({ kind: "new-project" })}>
              <Plus />
              New project
            </Button>
          </div>
        ) : (
          <div className="mx-auto grid max-w-4xl gap-3 sm:grid-cols-2">
            {projects.map((project) => (
              <ItemMenu
                key={project.id}
                actions={actionsFor(project)}
                kebabLabel={`Actions for ${project.name}`}
                className="rounded-lg"
              >
                <button
                  type="button"
                  onClick={() => router.push(hrefFor(project))}
                  className="group flex w-full flex-col rounded-lg border border-border bg-surface p-4 text-left shadow-xs transition-colors hover:border-border-strong hover:bg-accent/40"
                >
                  <div className="flex items-start justify-between gap-3 pr-6">
                    <span className="flex min-w-0 items-center gap-2.5">
                      <FolderKanban className="size-4 shrink-0 text-brand" />
                      <span className="truncate text-[0.8125rem] font-semibold">{project.name}</span>
                    </span>
                    {project.access === "owner" ? (
                      <Badge variant="default">Owner</Badge>
                    ) : (
                      <Badge variant="brand">
                        <Users />
                        {project.access === "editor" ? "Editor" : "Viewer"}
                      </Badge>
                    )}
                  </div>

                  <p className="mt-2 line-clamp-2 min-h-[2.5rem] text-[0.8125rem] leading-relaxed text-muted-foreground">
                    {project.description || "No description."}
                  </p>

                  <span className="mt-2 text-2xs text-muted-foreground/80">
                    Updated {formatDistanceToNow(new Date(project.updated_at), { addSuffix: true })}
                  </span>
                </button>
              </ItemMenu>
            ))}
          </div>
        )}
      </div>

      <NameDialog
        open={dialog?.kind === "new-project"}
        onOpenChange={(open) => !open && setDialog(null)}
        title="New project"
        description="Folders and documents live inside a project. You will own it."
        label="Project name"
        placeholder="Lunar lander avionics"
        action="Create project"
        pending={busy}
        onSubmit={(name) => void create(name)}
      />

      <NameDialog
        open={dialog?.kind === "rename-project"}
        onOpenChange={(open) => !open && setDialog(null)}
        title="Rename project"
        label="Project name"
        initialValue={dialog?.kind === "rename-project" ? dialog.project.name : ""}
        action="Rename"
        pending={busy}
        onSubmit={(name) => dialog?.kind === "rename-project" && void rename(dialog.project, name)}
      />

      <ConfirmDialog
        open={dialog?.kind === "delete-project"}
        onOpenChange={(open) => !open && setDialog(null)}
        title={`Delete ${dialog?.kind === "delete-project" ? dialog.project.name : "this project"}?`}
        description="This deletes the project and every folder and document inside it, for everyone it is shared with. This cannot be undone."
        confirmLabel="Delete project"
        pending={busy}
        onConfirm={() => dialog?.kind === "delete-project" && void remove(dialog.project)}
      />
    </>
  );
}
