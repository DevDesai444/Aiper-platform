"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { DocumentEditor } from "@/components/editor/document-editor";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api } from "@/lib/api";
import type { DocumentDetail, Folder } from "@/lib/types";

/** The chain of folders from the project root down to this document. */
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

export default function ProjectDocumentPage() {
  const { projectId, documentId } = useParams<{ projectId: string; documentId: string }>();
  const router = useRouter();

  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    api
      .getDocument(documentId)
      .then((result) => !cancelled && setDocument(result))
      .catch((caught: unknown) => {
        if (cancelled) return;
        // No access is reported as 404 by the resolver and shown as such: the
        // UI must never say "no permission", which would confirm it exists.
        if (caught instanceof ApiError && caught.status === 404) setMissing(true);
        else setError(caught instanceof Error ? caught.message : "Something went wrong.");
      });

    // The breadcrumb is a nicety: if the tree cannot be read, the editor still
    // opens and the crumbs simply stay short.
    api
      .getProjectTree(projectId)
      .then((tree) => {
        if (cancelled) return;
        setProjectName(tree.project?.name ?? null);
        setFolders(tree.folders);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [documentId, projectId]);

  if (missing || error) {
    return (
      <div className="flex flex-1 items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <h1 className="text-[0.9375rem] font-semibold">
            {missing ? "Not found" : "Could not open this document"}
          </h1>
          <p className="mt-1.5 text-[0.8125rem] text-muted-foreground">
            {missing
              ? "This document does not exist, or it is not one you can open."
              : error}
          </p>
          <Button
            className="mt-4"
            size="sm"
            variant="outline"
            onClick={() => router.push(`/projects/${projectId}`)}
          >
            Back to the project
          </Button>
        </div>
      </div>
    );
  }

  if (!document) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="h-9 shrink-0 border-b border-border px-6 py-2">
          <Skeleton className="h-4 w-56" />
        </div>
        <div className="flex-1 space-y-4 px-6 py-6">
          <Skeleton className="h-8 w-72" />
          <Skeleton className="h-[60vh] w-full" />
        </div>
      </div>
    );
  }

  const crumbs = ancestry(folders, document.folder_id);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <nav
        aria-label="Breadcrumb"
        className="flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-6 text-2xs text-muted-foreground"
      >
        <Link href="/projects" className="shrink-0 transition-colors hover:text-foreground">
          Projects
        </Link>
        <ChevronRight className="size-3 shrink-0 opacity-50" />
        <Link
          href={`/projects/${projectId}`}
          className="shrink-0 font-medium transition-colors hover:text-foreground"
        >
          {projectName ?? "Project"}
        </Link>
        {crumbs.map((folder) => (
          <span key={folder.id} className="flex shrink-0 items-center gap-1">
            <ChevronRight className="size-3 opacity-50" />
            <span>{folder.name}</span>
          </span>
        ))}
        <ChevronRight className="size-3 shrink-0 opacity-50" />
        <span className="truncate text-foreground">{document.title}</span>
      </nav>

      <DocumentEditor initial={document} backHref={`/projects/${projectId}`} />
    </div>
  );
}
