"use client";

import { FolderClosed, FolderKanban } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Folder } from "@/lib/types";
import { cn } from "@/lib/utils";

interface FolderNode {
  folder: Folder;
  depth: number;
}

/** All folders, indented by depth, in a stable parent-before-child order —
 * excluding `excludeId` and everything under it, so a folder is never offered
 * as its own destination. Depth-capped the same way the backend's own
 * ancestor walk is, so a cycle already in the data cannot hang this. */
function flattenExcluding(folders: Folder[], excludeId: string | null): FolderNode[] {
  const byParent = new Map<string | null, Folder[]>();
  for (const folder of folders) {
    const key = folder.parent_folder_id;
    const siblings = byParent.get(key) ?? [];
    siblings.push(folder);
    byParent.set(key, siblings);
  }
  for (const siblings of byParent.values()) siblings.sort((a, b) => a.name.localeCompare(b.name));

  const excluded = new Set<string>();
  if (excludeId) {
    const stack = [excludeId];
    for (let guard = 0; guard < 500 && stack.length; guard += 1) {
      const id = stack.pop() as string;
      if (excluded.has(id)) continue;
      excluded.add(id);
      for (const child of byParent.get(id) ?? []) stack.push(child.id);
    }
  }

  const rows: FolderNode[] = [];
  function walk(parentId: string | null, depth: number) {
    for (const folder of byParent.get(parentId) ?? []) {
      if (excluded.has(folder.id)) continue;
      rows.push({ folder, depth });
      walk(folder.id, depth + 1);
    }
  }
  walk(null, 0);
  return rows;
}

export function MoveDialog({
  open,
  onOpenChange,
  folders,
  currentFolderId,
  excludeFolderId = null,
  itemLabel,
  pending,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  folders: Folder[];
  currentFolderId: string | null;
  /** Moving a folder: itself and its own subtree cannot be the destination. */
  excludeFolderId?: string | null;
  itemLabel: string;
  pending?: boolean;
  onSubmit: (destination: string | null) => void;
}) {
  const [selected, setSelected] = useState<string | null>(currentFolderId);

  useEffect(() => {
    if (open) setSelected(currentFolderId);
  }, [open, currentFolderId]);

  const rows = useMemo(
    () => flattenExcluding(folders, excludeFolderId),
    [folders, excludeFolderId],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Move {itemLabel}</DialogTitle>
          <DialogDescription>Choose where it goes.</DialogDescription>
        </DialogHeader>

        <div className="max-h-72 space-y-0.5 overflow-y-auto rounded-lg border border-border bg-surface-sunken p-1.5">
          <button
            type="button"
            onClick={() => setSelected(null)}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[0.8125rem] transition-colors",
              selected === null
                ? "bg-surface text-foreground shadow-xs"
                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
            )}
          >
            <FolderKanban className="size-3.5 shrink-0 text-brand" />
            Project root
          </button>
          {rows.map(({ folder, depth }) => (
            <button
              key={folder.id}
              type="button"
              onClick={() => setSelected(folder.id)}
              style={{ paddingLeft: 10 + depth * 16 }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md py-1.5 pr-2.5 text-left text-[0.8125rem] transition-colors",
                selected === folder.id
                  ? "bg-surface text-foreground shadow-xs"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}
            >
              <FolderClosed className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{folder.name}</span>
            </button>
          ))}
        </div>

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            loading={pending}
            disabled={selected === currentFolderId}
            onClick={() => onSubmit(selected)}
          >
            Move here
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
