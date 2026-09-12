"use client";

import { formatDistanceToNow } from "date-fns";
import { FileText, GitBranch, Plus, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export default function EditorIndexPage() {
  const router = useRouter();
  const { documents, refreshDocuments } = useWorkspace();
  const [creating, setCreating] = useState(false);

  async function create() {
    setCreating(true);
    try {
      const created = await api.createDocument({ title: "Untitled document" });
      await refreshDocuments();
      router.push(`/editor/${created.id}`);
    } catch (error) {
      toast.error("Could not create the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Traceability Editor"
        description="Versioned documents — commit, diff, restore, share"
        actions={
          <Button size="sm" onClick={create} loading={creating}>
            <Plus />
            New document
          </Button>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {documents.length === 0 ? (
          <div className="mx-auto max-w-md rounded-xl border border-dashed border-border bg-surface px-8 py-14 text-center">
            <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border bg-surface-sunken">
              <GitBranch className="size-4 text-muted-foreground" />
            </div>
            <h2 className="mt-4 text-[0.9375rem] font-semibold">No documents yet</h2>
            <p className="mx-auto mt-1.5 max-w-xs text-balance text-[0.8125rem] leading-relaxed text-muted-foreground">
              Start one here, or send a draft over from the chat with{" "}
              <span className="font-medium text-foreground">Open in editor</span>.
            </p>
            <Button className="mt-5" size="sm" onClick={create} loading={creating}>
              <Plus />
              New document
            </Button>
          </div>
        ) : (
          <div className="mx-auto max-w-4xl overflow-hidden rounded-lg border border-border bg-surface">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border bg-surface-sunken/60">
                  <th className="px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                    Document
                  </th>
                  <th className="w-28 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                    Commits
                  </th>
                  <th className="w-36 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                    Updated
                  </th>
                  <th className="w-24 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                    Access
                  </th>
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <tr
                    key={document.id}
                    onClick={() => router.push(`/editor/${document.id}`)}
                    className="cursor-pointer border-b border-border transition-colors last:border-b-0 hover:bg-accent/50"
                  >
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-2.5">
                        <FileText className="size-4 shrink-0 text-muted-foreground" />
                        <span className="truncate text-[0.8125rem] font-medium">
                          {document.title}
                        </span>
                      </span>
                    </td>
                    <td className="tabular px-4 py-3 text-[0.8125rem] text-muted-foreground">
                      {document.revision_count}
                    </td>
                    <td className="px-4 py-3 text-[0.8125rem] text-muted-foreground">
                      {formatDistanceToNow(new Date(document.updated_at), { addSuffix: true })}
                    </td>
                    <td className="px-4 py-3">
                      {document.access === "owner" ? (
                        <Badge variant="default">Owner</Badge>
                      ) : (
                        <Badge variant="brand">
                          <Users />
                          Shared
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
