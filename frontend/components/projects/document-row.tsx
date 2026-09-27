"use client";

import { formatDistanceToNow } from "date-fns";
import { FileText } from "lucide-react";

import { ItemMenu, type MenuAction } from "@/components/projects/item-menu";
import type { TreeDocument } from "@/lib/types";

/** A document row — the list half of the Drive layout, folders above it. */
export function DocumentRow({
  document,
  onOpen,
  actions,
}: {
  document: TreeDocument;
  onOpen: () => void;
  actions: MenuAction[];
}) {
  return (
    <ItemMenu actions={actions} kebabLabel={`Actions for ${document.title}`}>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 pr-10 text-left transition-colors hover:border-border hover:bg-accent/40"
      >
        <FileText className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-[0.8125rem] font-medium">
          {document.title}
        </span>
        {document.revision_count > 0 ? (
          <span className="tabular shrink-0 text-2xs text-muted-foreground/70">
            r{document.revision_count}
          </span>
        ) : null}
        <span className="shrink-0 text-2xs text-muted-foreground/80">
          {formatDistanceToNow(new Date(document.updated_at), { addSuffix: true })}
        </span>
      </button>
    </ItemMenu>
  );
}
