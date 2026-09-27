/**
 * Opens a project's or document's audit trail ("Activity") in its own tab —
 * a reference view, not a navigation away from whatever the caller was
 * doing. Shared by every place an Activity menu item appears (the projects
 * list, the in-project header, the document row menu). Named apart from
 * `lib/activity.ts`, which is the unrelated chat agent's reasoning-trace
 * feed (`ActivityRow`/`toRows`) — same English word, different feature.
 */
export function openActivityLog(kind: "project" | "document", slug: string) {
  window.open(`/logs/${kind}/${slug}`, "_blank", "noopener,noreferrer");
}
