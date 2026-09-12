"use client";

import { Check, Clock, Mail, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import type { Collaborator, DocumentDetail } from "@/lib/types";

export function ShareDialog({
  document,
  canManage,
  onChange,
}: {
  document: DocumentDetail;
  canManage: boolean;
  onChange: (collaborators: Collaborator[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"editor" | "viewer">("editor");
  const [pending, setPending] = useState(false);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    try {
      const created = await api.addCollaborator(document.id, { email: email.trim(), role });
      onChange([...document.collaborators, created]);
      setEmail("");
      toast.success(
        created.invite_status === "accepted"
          ? `Shared with ${created.email}`
          : `Invitation stored for ${created.email}`,
        {
          description:
            created.invite_status === "accepted"
              ? undefined
              : "They will be connected automatically when they register.",
        },
      );
    } catch (error) {
      toast.error("Could not share the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setPending(false);
    }
  }

  async function remove(collaborator: Collaborator) {
    try {
      await api.removeCollaborator(document.id, collaborator.id);
      onChange(document.collaborators.filter((c) => c.id !== collaborator.id));
    } catch (error) {
      toast.error("Could not remove that collaborator", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Users />
          Share
          {document.collaborators.length > 0 ? (
            <span className="tabular text-muted-foreground">{document.collaborators.length}</span>
          ) : null}
        </Button>
      </DialogTrigger>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Share “{document.title}”</DialogTitle>
          <DialogDescription>
            Add a collaborator by email address. No mail is sent and no code is issued — the
            invitation is stored, and is connected to the account when that address registers.
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
              Invite
            </Button>
          </form>
        ) : null}

        <div className="mt-4 space-y-1">
          <div className="flex items-center justify-between rounded-md bg-surface-sunken px-3 py-2">
            <span className="truncate text-[0.8125rem]">{document.owner_email}</span>
            <span className="text-2xs text-muted-foreground">Owner</span>
          </div>

          {document.collaborators.map((collaborator) => (
            <div
              key={collaborator.id}
              className="group flex items-center justify-between gap-2 rounded-md px-3 py-2 transition-colors hover:bg-accent/60"
            >
              <span className="flex min-w-0 items-center gap-2">
                <Mail className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate text-[0.8125rem]">{collaborator.email}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <span className="inline-flex items-center gap-1 text-2xs text-muted-foreground">
                  {collaborator.invite_status === "accepted" ? (
                    <Check className="size-3 text-verdict-pass" />
                  ) : (
                    <Clock className="size-3 text-verdict-partial" />
                  )}
                  {collaborator.invite_status === "accepted" ? "connected" : "pending"}
                </span>
                <span className="text-2xs capitalize text-muted-foreground">
                  {collaborator.role}
                </span>
                {canManage ? (
                  <button
                    type="button"
                    onClick={() => void remove(collaborator)}
                    className="rounded p-1 text-muted-foreground opacity-0 transition-all hover:bg-accent hover:text-destructive group-hover:opacity-100"
                    aria-label={`Remove ${collaborator.email}`}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                ) : null}
              </span>
            </div>
          ))}

          {document.collaborators.length === 0 ? (
            <p className="px-3 py-4 text-center text-2xs text-muted-foreground">
              Not shared with anyone yet.
            </p>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
