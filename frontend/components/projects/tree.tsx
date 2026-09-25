"use client";

import {
  ChevronRight,
  FileText,
  FolderClosed,
  FolderOpen,
  FolderPlus,
  MoreHorizontal,
  Plus,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Folder, TreeDocument } from "@/lib/types";
import { cn } from "@/lib/utils";

/** A folder with its children resolved — the shape the rows actually render. */
interface TreeNode {
  folder: Folder;
  children: TreeNode[];
  documents: TreeDocument[];
}

/**
 * Build the forest from the flat lists the API returns.
 *
 * A folder whose parent is missing is treated as a root. That is not a defensive
 * flourish: the caller may hold a grant on a subfolder without being able to see
 * the folder above it, so the API legitimately returns children without parents.
 */
export function buildForest(folders: Folder[], documents: TreeDocument[]) {
  const nodes = new Map<string, TreeNode>(
    folders.map((folder) => [folder.id, { folder, children: [], documents: [] }]),
  );

  for (const document of documents) {
    if (document.folder_id) {
      const parent = nodes.get(document.folder_id);
      if (parent) {
        parent.documents.push(document);
        continue;
      }
    }
    // Falls through to the project root: either folder_id is null, or its
    // folder is one the caller cannot see.
  }

  const roots: TreeNode[] = [];
  for (const node of nodes.values()) {
    const parentId = node.folder.parent_folder_id;
    const parent = parentId ? nodes.get(parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  // A folder has exactly one parent, so a parent_folder_id cycle forms a ring
  // in which every member has a visible parent. No member is ever a root, so a
  // cycle drops out of the render entirely rather than recursing forever.

  const byName = (a: TreeNode, b: TreeNode) => a.folder.name.localeCompare(b.folder.name);
  const byTitle = (a: TreeDocument, b: TreeDocument) => a.title.localeCompare(b.title);
  const sort = (list: TreeNode[]) => {
    list.sort(byName);
    for (const node of list) {
      node.documents.sort(byTitle);
      sort(node.children);
    }
  };
  sort(roots);

  const visibleFolderIds = new Set(nodes.keys());
  const rootDocuments = documents
    .filter((document) => !document.folder_id || !visibleFolderIds.has(document.folder_id))
    .sort(byTitle);

  return { roots, rootDocuments };
}

const INDENT = 14;

function DocumentRow({
  document,
  depth,
  onOpen,
}: {
  document: TreeDocument;
  depth: number;
  onOpen: (document: TreeDocument) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(document)}
      style={{ paddingLeft: 8 + depth * INDENT }}
      className="flex w-full items-center gap-2 rounded-md py-[6px] pr-2 text-left text-[0.8125rem] text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
    >
      <FileText className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{document.title}</span>
      {document.revision_count > 0 ? (
        <span className="tabular shrink-0 text-2xs text-muted-foreground/70">
          r{document.revision_count}
        </span>
      ) : null}
    </button>
  );
}

function FolderRow({
  node,
  depth,
  canEdit,
  onOpen,
  onNewFolder,
  onNewDocument,
}: {
  node: TreeNode;
  depth: number;
  canEdit: boolean;
  onOpen: (document: TreeDocument) => void;
  onNewFolder: (parent: Folder) => void;
  onNewDocument: (folder: Folder) => void;
}) {
  const [open, setOpen] = useState(true);
  const empty = node.children.length === 0 && node.documents.length === 0;

  return (
    <li>
      <div className="group/row flex items-center">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          style={{ paddingLeft: 8 + depth * INDENT }}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md py-[6px] pr-1 text-left text-[0.8125rem] font-medium text-foreground transition-colors hover:bg-accent/60"
          aria-expanded={open}
        >
          <ChevronRight
            className={cn(
              "size-3 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
              empty && "opacity-30",
            )}
          />
          {open ? (
            <FolderOpen className="size-3.5 shrink-0 text-brand" />
          ) : (
            <FolderClosed className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate">{node.folder.name}</span>
        </button>

        {canEdit ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Actions for ${node.folder.name}`}
                className="opacity-0 transition-opacity group-hover/row:opacity-100 data-[state=open]:opacity-100"
              >
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => onNewDocument(node.folder)}>
                <Plus />
                New document here
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onNewFolder(node.folder)}>
                <FolderPlus />
                New subfolder
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {open && !empty ? (
        <ul>
          {node.children.map((child) => (
            <FolderRow
              key={child.folder.id}
              node={child}
              depth={depth + 1}
              canEdit={canEdit}
              onOpen={onOpen}
              onNewFolder={onNewFolder}
              onNewDocument={onNewDocument}
            />
          ))}
          {node.documents.map((document) => (
            <li key={document.id}>
              <DocumentRow document={document} depth={depth + 1} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function ProjectTreeView({
  folders,
  documents,
  canEdit,
  onOpen,
  onNewFolder,
  onNewDocument,
}: {
  folders: Folder[];
  documents: TreeDocument[];
  canEdit: boolean;
  onOpen: (document: TreeDocument) => void;
  /** `null` parent means a folder at the project root. */
  onNewFolder: (parent: Folder | null) => void;
  /** `null` folder means a document at the project root. */
  onNewDocument: (folder: Folder | null) => void;
}) {
  const { roots, rootDocuments } = useMemo(
    () => buildForest(folders, documents),
    [folders, documents],
  );

  if (roots.length === 0 && rootDocuments.length === 0) {
    return (
      <p className="px-3 py-2 text-2xs leading-relaxed text-muted-foreground/80">
        Nothing here yet.
        {canEdit ? " Add a folder or a document to get started." : null}
      </p>
    );
  }

  return (
    <ul className="space-y-px">
      {roots.map((node) => (
        <FolderRow
          key={node.folder.id}
          node={node}
          depth={0}
          canEdit={canEdit}
          onOpen={onOpen}
          onNewFolder={onNewFolder}
          onNewDocument={onNewDocument}
        />
      ))}
      {rootDocuments.map((document) => (
        <li key={document.id}>
          <DocumentRow document={document} depth={0} onOpen={onOpen} />
        </li>
      ))}
    </ul>
  );
}
