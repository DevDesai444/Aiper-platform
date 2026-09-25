"use client";

import { formatDistanceToNow } from "date-fns";
import { FolderKanban, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { NameDialog } from "@/components/projects/name-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export default function ProjectsPage() {
  const router = useRouter();
  const { projects, refreshProjects } = useWorkspace();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  async function create(name: string) {
    setCreating(true);
    try {
      const project = await api.createProject({ name });
      await refreshProjects();
      setDialogOpen(false);
      router.push(`/projects/${project.id}`);
    } catch (error) {
      toast.error("Could not create the project", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Projects"
        description="Everything you work on lives in a project"
        actions={
          <Button size="sm" onClick={() => setDialogOpen(true)}>
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
            <Button className="mt-5" size="sm" onClick={() => setDialogOpen(true)}>
              <Plus />
              New project
            </Button>
          </div>
        ) : (
          <div className="mx-auto grid max-w-4xl gap-3 sm:grid-cols-2">
            {projects.map((project) => (
              <Link
                key={project.id}
                href={`/projects/${project.id}`}
                className="group flex flex-col rounded-lg border border-border bg-surface p-4 shadow-xs transition-colors hover:border-border-strong hover:bg-accent/40"
              >
                <div className="flex items-start justify-between gap-3">
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
              </Link>
            ))}
          </div>
        )}
      </div>

      <NameDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        title="New project"
        description="Folders and documents live inside a project. You will own it."
        label="Project name"
        placeholder="Lunar lander avionics"
        action="Create project"
        pending={creating}
        onSubmit={(name) => void create(name)}
      />
    </>
  );
}
