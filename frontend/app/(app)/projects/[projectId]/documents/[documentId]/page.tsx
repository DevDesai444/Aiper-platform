"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/layout/logo";
import { api } from "@/lib/api";
import { extractRef, idSlug } from "@/lib/slug";

/**
 * The `/documents/` segment is gone — documents live at `/d/` now, the
 * shorter Drive-style path. Old links (bookmarks, chat history minted before
 * this rename) still carry `/documents/`, so this resolves the document and
 * forwards to its new home rather than 404ing on a path that used to work.
 */
export default function LegacyDocumentsSegmentRedirect() {
  const { documentId: rawDocumentParam } = useParams<{ documentId: string }>();
  const documentRef = extractRef(rawDocumentParam);
  const router = useRouter();

  useEffect(() => {
    if (!documentRef) {
      router.replace("/projects");
      return;
    }
    let cancelled = false;
    api
      .getDocument(documentRef)
      .then((document) => {
        if (cancelled) return;
        // The project's own name is not known here; that segment lands as a
        // bare id and the /d/ page's own canonical check upgrades it once it
        // loads the project's tree.
        router.replace(
          document.project_id
            ? `/projects/${document.project_id}/d/${idSlug(document.title, document.id)}`
            : "/projects",
        );
      })
      .catch(() => !cancelled && router.replace("/projects"));
    return () => {
      cancelled = true;
    };
  }, [documentRef, router]);

  return (
    <div className="flex flex-1 items-center justify-center">
      <Logo className="size-6 animate-pulse opacity-40" />
    </div>
  );
}
