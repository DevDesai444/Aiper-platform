"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/layout/logo";
import { api } from "@/lib/api";

/**
 * The flat /editor list is gone: documents live in projects now. Old links are
 * still handed out in chat history and bookmarks, so this resolves the document
 * to its project and forwards to the project-scoped route.
 */
export default function LegacyDocumentRedirect() {
  const { documentId } = useParams<{ documentId: string }>();
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    api
      .getDocument(documentId)
      .then((document) => {
        if (cancelled) return;
        router.replace(
          document.project_id
            ? `/projects/${document.project_id}/documents/${document.id}`
            : "/projects",
        );
      })
      .catch(() => !cancelled && router.replace("/projects"));
    return () => {
      cancelled = true;
    };
  }, [documentId, router]);

  return (
    <div className="flex flex-1 items-center justify-center">
      <Logo className="size-6 animate-pulse opacity-40" />
    </div>
  );
}
