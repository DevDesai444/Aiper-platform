"use client";

import { format, formatDistanceToNow } from "date-fns";
import { History } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import type { AuditEntry } from "@/lib/types";

// audit.py's event vocabulary, in the same order it's declared there. Not
// exhaustive by design — an action this map doesn't know yet still renders,
// just via the fallback below, so a new constant there never breaks this page.
const ACTION_LABELS: Record<string, string> = {
  "auth.register": "Registered",
  "auth.login": "Logged in",
  "auth.provision": "Provisioned the account",
  "project.create": "Created the project",
  "project.rename": "Renamed the project",
  "project.delete": "Deleted the project",
  "folder.create": "Created a folder",
  "folder.rename": "Renamed a folder",
  "folder.move": "Moved a folder",
  "folder.delete": "Deleted a folder",
  "document.create": "Created the document",
  "document.rename": "Renamed the document",
  "document.move": "Moved the document",
  "document.delete": "Deleted the document",
  "document.commit": "Committed a revision",
  "document.restore": "Restored a revision",
  "share.grant": "Shared access",
  "share.revoke": "Revoked access",
  "file.upload": "Uploaded a file",
  "file.delete": "Deleted a file",
  "template.create": "Created a template",
  "template.delete": "Deleted a template",
};

function describe(action: string): string {
  return ACTION_LABELS[action] ?? action.replace(/[._]/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * The activity log for a project or document — same list either side, only
 * the fetch and the heading differ. `entries` is `null` while loading,
 * `"missing"` when the subject does not exist or the caller cannot see it
 * (indistinguishable by design, same as everywhere else in this app), and an
 * array (possibly empty) once loaded.
 */
export function AuditLogView({
  title,
  entries,
}: {
  title: string;
  entries: AuditEntry[] | "missing" | null;
}) {
  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-6 flex items-center gap-2">
        <History className="size-4 text-muted-foreground" />
        <h1 className="text-[0.9375rem] font-semibold tracking-[-0.01em]">{title}</h1>
      </div>

      {entries === null ? (
        <div className="space-y-3">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : entries === "missing" ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          This does not exist, or it is not something you can open.
        </p>
      ) : entries.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">No activity yet.</p>
      ) : (
        <ol className="space-y-1">
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="flex items-baseline justify-between gap-4 rounded-lg border border-transparent px-3 py-2.5 text-[0.8125rem] hover:border-border hover:bg-accent/40"
            >
              <span className="min-w-0 truncate">
                {describe(entry.action)}
                <span className="text-muted-foreground">
                  {" "}
                  &middot; {entry.actor_name || entry.actor_email || "Someone"}
                </span>
              </span>
              <time
                dateTime={entry.created_at}
                title={format(new Date(entry.created_at), "PPpp")}
                className="shrink-0 text-2xs text-muted-foreground/80"
              >
                {formatDistanceToNow(new Date(entry.created_at), { addSuffix: true })}
              </time>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
