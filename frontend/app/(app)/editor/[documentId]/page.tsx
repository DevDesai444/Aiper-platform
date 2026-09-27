"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/layout/logo";
import { api } from "@/lib/api";
import { idSlug } from "@/lib/slug";

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
        // The project's own name is not known here; the project segment lands
        // as a bare id and the document page's own canonical check upgrades it
        // once it loads the project's tree.
        router.replace(
          document.project_id
            ? `/projects/${document.project_id}/documents/${idSlug(document.title, document.id)}`
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
