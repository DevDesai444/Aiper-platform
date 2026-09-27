"use client";

import { FolderClosed } from "lucide-react";

import { ItemMenu, type MenuAction } from "@/components/projects/item-menu";
import type { Folder } from "@/lib/types";

/** A folder tile — same card language as the projects landing: border,
 * surface background, subtle shadow, a hover state that invites a click. */
export function FolderCard({
  folder,
  onOpen,
  actions,
}: {
  folder: Folder;
  onOpen: () => void;
  actions: MenuAction[];
}) {
  return (
    <ItemMenu actions={actions} kebabLabel={`Actions for ${folder.name}`}>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full flex-col items-start gap-2.5 rounded-lg border border-border bg-surface p-4 text-left shadow-xs transition-colors hover:border-border-strong hover:bg-accent/40"
      >
        <FolderClosed className="size-5 shrink-0 text-brand" />
        <span className="w-full truncate text-[0.8125rem] font-semibold">{folder.name}</span>
      </button>
    </ItemMenu>
  );
}
