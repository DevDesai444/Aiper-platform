"use client";

import { formatDistanceToNow } from "date-fns";
import { Crosshair, FileText, Layers, Trash2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { UploadZone } from "@/components/vault/upload-zone";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { FileAsset } from "@/lib/types";
import { formatBytes } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

export default function VaultPage() {
  const { files, refreshFiles } = useWorkspace();

  const indexedPages = files.reduce((total, file) => total + file.page_count, 0);

  async function toggleRole(file: FileAsset) {
    try {
      await api.setFileRole(file.id, file.comparison_role === "target" ? "source" : "target");
      await refreshFiles();
    } catch (error) {
      toast.error("Could not change the role", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  async function remove(file: FileAsset) {
    try {
      await api.deleteFile(file.id);
      await refreshFiles();
      toast.success(`${file.filename} removed`, {
        description: "Its indexed pages were deleted from the vector store.",
      });
    } catch (error) {
      toast.error("Could not remove that file", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  return (
    <>
      <PageHeader
        title="Document Vault"
        description="Everything the agent can cite"
        actions={
          <Badge variant="default">
            <Layers />
            {indexedPages} indexed {indexedPages === 1 ? "page" : "pages"}
          </Badge>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          <UploadZone onUploaded={() => void refreshFiles()} />

          {files.length > 0 ? (
            <div className="overflow-hidden rounded-lg border border-border bg-surface">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border bg-surface-sunken/60">
                    <th className="px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Source
                    </th>
                    <th className="w-24 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Pages
                    </th>
                    <th className="w-24 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Size
                    </th>
                    <th className="w-28 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Role
                    </th>
                    <th className="w-32 px-4 py-2.5 text-left text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Added
                    </th>
                    <th className="w-12" />
                  </tr>
                </thead>
                <tbody>
                  {files.map((file) => (
                    <tr
                      key={file.id}
                      className="group border-b border-border transition-colors last:border-b-0 hover:bg-accent/40"
                    >
                      <td className="px-4 py-3">
                        <span className="flex items-center gap-2.5">
                          {file.indexed ? (
                            <FileText className="size-4 shrink-0 text-muted-foreground" />
                          ) : (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <TriangleAlert className="size-4 shrink-0 text-verdict-partial" />
                              </TooltipTrigger>
                              <TooltipContent>
                                {file.index_error ?? "This file could not be indexed."}
                              </TooltipContent>
                            </Tooltip>
                          )}
                          <span className="min-w-0">
                            <span className="block truncate text-[0.8125rem] font-medium">
                              {file.filename}
                            </span>
                            {!file.indexed && file.index_error ? (
                              <span className="block truncate text-2xs text-verdict-partial">
                                {file.index_error}
                              </span>
                            ) : null}
                          </span>
                        </span>
                      </td>
                      <td className="tabular px-4 py-3 text-[0.8125rem] text-muted-foreground">
                        {file.page_count || "—"}
                      </td>
                      <td className="tabular px-4 py-3 text-[0.8125rem] text-muted-foreground">
                        {formatBytes(file.size_bytes)}
                      </td>
                      <td className="px-4 py-3">
                        <button type="button" onClick={() => void toggleRole(file)}>
                          {file.comparison_role === "target" ? (
                            <Badge variant="brand">
                              <Crosshair />
                              Target
                            </Badge>
                          ) : (
                            <Badge variant="outline">Source</Badge>
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-[0.8125rem] text-muted-foreground">
                        {formatDistanceToNow(new Date(file.created_at), { addSuffix: true })}
                      </td>
                      <td className="px-2 py-3">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="opacity-0 transition-opacity group-hover:opacity-100"
                          onClick={() => void remove(file)}
                          aria-label={`Remove ${file.filename}`}
                        >
                          <Trash2 />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <p className="text-2xs leading-relaxed text-muted-foreground">
            Retrieval is page-level by design: one page is one chunk, so every citation names a page
            a reviewer can open. A scanned PDF with no text layer cannot be indexed — it is flagged
            here rather than failing silently.
          </p>
        </div>
      </div>
    </>
  );
}
