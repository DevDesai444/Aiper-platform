"use client";

import { Mail, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Project, ProjectMember } from "@/lib/types";

/**
 * Project sharing: access_grants directly, no pending-invite-by-email
 * concept the way document sharing has one -- a share target must already
 * be a real user in the caller's own org, or the add fails with a clean
 * error rather than something silently inert.
 *
 * Always externally controlled — a project's kebab/context menu opens this,
 * there is no trigger button of its own to render (unlike the document
 * ShareDialog, which still renders its own in the editor header).
 */
export function ProjectShareDialog({
  project,
  canManage,
  open,
  onOpenChange,
}: {
  project: Project;
  canManage: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [members, setMembers] = useState<ProjectMember[] | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"editor" | "viewer">("editor");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMembers(null);
    api
      .listProjectMembers(project.id)
      .then(setMembers)
      .catch((error: unknown) => {
        toast.error("Could not load members", {
          description: error instanceof Error ? error.message : undefined,
        });
        onOpenChange(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project.id]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    try {
      const member = await api.addProjectMember(project.id, { email: email.trim(), role });
      setMembers((current) => [...(current ?? []), member]);
      setEmail("");
      toast.success(`Shared with ${member.email}`);
    } catch (error) {
      toast.error("Could not share the project", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setPending(false);
    }
  }

  async function remove(member: ProjectMember) {
    try {
      await api.removeProjectMember(project.id, member.user_id);
      setMembers((current) => current?.filter((m) => m.user_id !== member.user_id) ?? null);
    } catch (error) {
      toast.error("Could not remove that member", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Share &ldquo;{project.name}&rdquo;</DialogTitle>
          <DialogDescription>
            Add a member by email address. They must already have an account in your
            organisation — this does not send an invitation.
          </DialogDescription>
        </DialogHeader>

        {canManage ? (
          <form onSubmit={add} className="flex items-center gap-2">
            <Input
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="colleague@agency.int"
              className="flex-1"
            />
            <Select value={role} onValueChange={(value) => setRole(value as "editor" | "viewer")}>
              <SelectTrigger className="w-[104px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="editor">Editor</SelectItem>
                <SelectItem value="viewer">Viewer</SelectItem>
              </SelectContent>
            </Select>
            <Button type="submit" loading={pending}>
              Share
            </Button>
          </form>
        ) : null}

        <div className="mt-4 space-y-1">
          {members === null ? (
            <>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </>
          ) : (
            members.map((member) => (
              <div
                key={member.user_id}
                className="group flex items-center justify-between gap-2 rounded-md px-3 py-2 transition-colors hover:bg-accent/60"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Mail className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate text-[0.8125rem]">
                    {member.full_name || member.email}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="text-2xs capitalize text-muted-foreground">{member.role}</span>
                  {canManage && member.role !== "owner" ? (
                    <button
                      type="button"
                      onClick={() => void remove(member)}
                      className="rounded p-1 text-muted-foreground opacity-0 transition-all hover:bg-accent hover:text-destructive group-hover:opacity-100"
                      aria-label={`Remove ${member.email}`}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  ) : null}
                </span>
              </div>
            ))
          )}

          {members !== null && members.length === 0 ? (
            <p className="flex items-center justify-center gap-1.5 px-3 py-4 text-center text-2xs text-muted-foreground">
              <Users className="size-3.5" />
              Not shared with anyone yet.
            </p>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
