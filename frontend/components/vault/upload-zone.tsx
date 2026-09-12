"use client";

import { Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { FileAsset } from "@/lib/types";
import { cn } from "@/lib/utils";

export function UploadZone({ onUploaded }: { onUploaded: (file: FileAsset) => void }) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    setBusy((count) => count + list.length);
    for (const file of list) {
      try {
        const asset = await api.uploadFile(file);
        onUploaded(asset);
        if (asset.indexed) {
          toast.success(`${asset.filename} indexed`, {
            description: `${asset.page_count} ${asset.page_count === 1 ? "page" : "pages"} — one page is one retrieval chunk.`,
          });
        } else {
          toast.warning(`${asset.filename} could not be indexed`, {
            description: asset.index_error ?? undefined,
          });
        }
      } catch (error) {
        toast.error(`Could not upload ${file.name}`, {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setBusy((count) => count - 1);
      }
    }
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (event.dataTransfer.files.length) void upload(event.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      className={cn(
        "cursor-pointer rounded-xl border border-dashed bg-surface px-6 py-9 text-center transition-colors",
        dragging ? "border-brand/60 bg-brand-subtle/40" : "border-border hover:border-border-strong",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.pptx,.docx,.txt,.md"
        className="hidden"
        onChange={(event) => {
          if (event.target.files?.length) void upload(event.target.files);
          event.target.value = "";
        }}
      />

      <div className="mx-auto flex size-9 items-center justify-center rounded-lg border border-border bg-surface-sunken">
        {busy > 0 ? (
          <Loader2 className="size-4 animate-spin text-brand" />
        ) : (
          <Upload className="size-4 text-muted-foreground" />
        )}
      </div>

      <p className="mt-3 text-[0.8125rem] font-medium">
        {busy > 0 ? `Indexing ${busy} file${busy === 1 ? "" : "s"}…` : "Drop sources here"}
      </p>
      <p className="mt-1 text-2xs text-muted-foreground">
        PDF · PPTX · DOCX · TXT — each page becomes one citable chunk
      </p>
    </div>
  );
}
