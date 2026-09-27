"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AuditLogView } from "@/components/logs/audit-log-view";
import { api } from "@/lib/api";
import { extractRef } from "@/lib/slug";
import type { AuditEntry } from "@/lib/types";

export default function DocumentLogsPage() {
  const { ref: rawRef } = useParams<{ ref: string }>();
  const ref = extractRef(rawRef);
  const [entries, setEntries] = useState<AuditEntry[] | "missing" | null>(null);

  useEffect(() => {
    if (!ref) {
      setEntries("missing");
      return;
    }
    let cancelled = false;
    api
      .getDocumentAuditLog(ref)
      .then((result) => !cancelled && setEntries(result))
      // No access and "does not exist" render identically here, same as the
      // document page itself — this is a reference tab, not somewhere to
      // retry a transient failure from.
      .catch(() => !cancelled && setEntries("missing"));
    return () => {
      cancelled = true;
    };
  }, [ref]);

  return <AuditLogView title="Document activity" entries={entries} />;
}
