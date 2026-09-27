"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AuditLogView } from "@/components/logs/audit-log-view";
import { api } from "@/lib/api";
import { extractRef } from "@/lib/slug";
import type { AuditEntry } from "@/lib/types";

export default function ProjectLogsPage() {
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
      .getProjectAuditLog(ref)
      .then((result) => !cancelled && setEntries(result))
      .catch(() => !cancelled && setEntries("missing"));
    return () => {
      cancelled = true;
    };
  }, [ref]);

  return <AuditLogView title="Project activity" entries={entries} />;
}
