"use client";

import { formatDistanceToNow } from "date-fns";
import { Bot, History, RotateCcw, User as UserIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DiffStat, DiffView } from "@/components/editor/diff-view";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { Diff, DocumentDetail, Revision } from "@/lib/types";
import { cn } from "@/lib/utils";

export function HistoryPanel({
  document,
  readOnly,
  onRestored,
}: {
  document: DocumentDetail;
  readOnly: boolean;
  onRestored: (next: DocumentDetail) => void;
}) {
  const [selected, setSelected] = useState<Revision | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [restoring, setRestoring] = useState(false);

  async function open(revision: Revision) {
    setSelected(revision);
    setDiff(null);
    try {
      setDiff(await api.getDiff(document.id, revision.id));
    } catch (error) {
      toast.error("Could not load that diff", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  async function restore() {
    if (!selected) return;
    setRestoring(true);
    try {
      onRestored(await api.restore(document.id, selected.id));
      toast.success(`Revision ${selected.revision_number} restored`, {
        description: "Restoring appends a new commit — nothing was erased.",
      });
      setSelected(null);
    } catch (error) {
      toast.error("Could not restore that revision", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setRestoring(false);
    }
  }

  return (
    <>
      <aside className="flex w-[282px] shrink-0 flex-col border-l border-border bg-surface-sunken">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <History className="size-3.5 text-muted-foreground" />
          <span className="text-[0.8125rem] font-medium">History</span>
          <span className="tabular ml-auto text-2xs text-muted-foreground">
            {document.revision_count} {document.revision_count === 1 ? "commit" : "commits"}
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          <ol className="relative space-y-0.5">
            {/* The spine. */}
            <span className="absolute bottom-2 left-[7px] top-2 w-px bg-border" aria-hidden />

            {document.revisions.map((revision, index) => (
              <li key={revision.id} className="relative">
                <button
                  type="button"
                  onClick={() => void open(revision)}
                  className="group flex w-full gap-2.5 rounded-md py-1.5 pl-0 pr-1.5 text-left transition-colors hover:bg-accent/60"
                >
                  <span
                    className={cn(
                      "relative z-10 mt-[5px] size-[15px] shrink-0 rounded-full border bg-surface",
                      index === 0
                        ? "border-brand/60 ring-2 ring-brand/15"
                        : "border-border-strong",
                    )}
                  >
                    {revision.source === "agent" ? (
                      <Bot className="absolute inset-0 m-auto size-2.5 text-muted-foreground" />
                    ) : (
                      <UserIcon className="absolute inset-0 m-auto size-2.5 text-muted-foreground" />
                    )}
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-1.5">
                      <span className="tabular text-2xs font-medium text-muted-foreground">
                        r{revision.revision_number}
                      </span>
                      <span className="truncate text-[0.8125rem] font-medium">
                        {revision.commit_message}
                      </span>
                    </span>
                    <span className="mt-0.5 flex items-center gap-2">
                      <span className="truncate text-2xs text-muted-foreground">
                        {revision.author_name || revision.author_email} ·{" "}
                        {formatDistanceToNow(new Date(revision.created_at), { addSuffix: true })}
                      </span>
                    </span>
                    <span className="mt-1 block">
                      <DiffStat
                        additions={revision.additions}
                        deletions={revision.deletions}
                        modifications={revision.modifications}
                      />
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </div>

        <p className="shrink-0 border-t border-border px-4 py-3 text-2xs leading-relaxed text-muted-foreground">
          History is append-only. Restoring an old revision adds a commit rather than rewriting
          anything.
        </p>
      </aside>

      <Dialog open={Boolean(selected)} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              <span className="tabular mr-2 text-muted-foreground">
                r{selected?.revision_number}
              </span>
              {selected?.commit_message}
            </DialogTitle>
            <DialogDescription>
              {selected?.author_name || selected?.author_email} ·{" "}
              {selected ? new Date(selected.created_at).toLocaleString() : ""} ·{" "}
              {selected?.source === "agent" ? "drafted by the agent" : "edited by a person"}
            </DialogDescription>
          </DialogHeader>

          {diff ? (
            <DiffView diff={diff} />
          ) : (
            <p className="py-10 text-center text-2xs text-muted-foreground">Loading the diff…</p>
          )}

          <DialogFooter>
            <Button variant="ghost" onClick={() => setSelected(null)}>
              Close
            </Button>
            {!readOnly && selected && selected.revision_number !== document.revision_count ? (
              <Button variant="outline" onClick={restore} loading={restoring}>
                <RotateCcw />
                Restore this revision
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
