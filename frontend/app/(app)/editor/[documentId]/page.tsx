"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { DocumentEditor } from "@/components/editor/document-editor";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";

export default function DocumentPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getDocument(documentId)
      .then((result) => !cancelled && setDocument(result))
      .catch((caught: Error) => !cancelled && setError(caught.message));
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center px-6">
        <p className="text-[0.8125rem] text-muted-foreground">{error}</p>
      </div>
    );
  }

  if (!document) {
    return (
      <div className="flex min-h-0 flex-1">
        <div className="flex-1 space-y-4 px-6 py-6">
          <Skeleton className="h-8 w-72" />
          <Skeleton className="h-[60vh] w-full" />
        </div>
        <div className="w-[282px] border-l border-border p-4">
          <Skeleton className="h-6 w-32" />
        </div>
      </div>
    );
  }

  return <DocumentEditor initial={document} />;
}
