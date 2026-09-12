"use client";

import {
  ArrowUp,
  Crosshair,
  FileText,
  Loader2,
  Paperclip,
  Square,
  TriangleAlert,
  X,
} from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import { ModeSelector } from "@/components/chat/mode-selector";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { DocumentTemplate, FileAsset, Mode } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPT = ".pdf,.pptx,.docx,.txt,.md";

function AttachmentChip({
  file,
  isTarget,
  comparison,
  onRemove,
  onMakeTarget,
}: {
  file: FileAsset;
  isTarget: boolean;
  comparison: boolean;
  onRemove: () => void;
  onMakeTarget: () => void;
}) {
  const failed = !file.indexed;

  return (
    <span
      className={cn(
        "group inline-flex max-w-[260px] items-center gap-1.5 rounded-md border bg-surface py-1 pl-2 pr-1 text-2xs shadow-xs transition-colors",
        failed
          ? "border-verdict-partial/40 bg-verdict-partial-bg text-verdict-partial"
          : isTarget
            ? "border-brand/45 text-foreground ring-1 ring-brand/20"
            : "border-border text-foreground",
      )}
    >
      {failed ? (
        <TriangleAlert className="size-3 shrink-0" />
      ) : isTarget ? (
        <Crosshair className="size-3 shrink-0 text-brand" />
      ) : (
        <FileText className="size-3 shrink-0 text-muted-foreground" />
      )}

      <Tooltip>
        <TooltipTrigger asChild>
          <span className="truncate font-medium">{file.filename}</span>
        </TooltipTrigger>
        <TooltipContent>
          {failed
            ? (file.index_error ?? "This file could not be indexed.")
            : `${file.page_count} ${file.page_count === 1 ? "page" : "pages"} indexed`}
        </TooltipContent>
      </Tooltip>

      {!failed ? (
        <span className="tabular shrink-0 text-muted-foreground">{file.page_count}p</span>
      ) : null}

      {comparison && !isTarget && !failed ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={onMakeTarget}
              className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-brand"
            >
              <Crosshair className="size-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent>Make this the target</TooltipContent>
        </Tooltip>
      ) : null}

      {isTarget ? (
        <span className="shrink-0 rounded border border-brand/30 bg-brand-subtle px-1 font-medium uppercase tracking-wide text-brand">
          target
        </span>
      ) : null}

      <button
        type="button"
        onClick={onRemove}
        className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        aria-label={`Remove ${file.filename}`}
      >
        <X className="size-3" />
      </button>
    </span>
  );
}

export function Composer({
  mode,
  onModeChange,
  templates,
  templateKey,
  onTemplateChange,
  attachments,
  onAttach,
  onRemoveAttachment,
  targetId,
  onTargetChange,
  onSubmit,
  onStop,
  running,
  sessionId,
}: {
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  templates: DocumentTemplate[];
  templateKey: string | null;
  onTemplateChange: (key: string) => void;
  attachments: FileAsset[];
  onAttach: (file: FileAsset) => void;
  onRemoveAttachment: (id: string) => void;
  targetId: string | null;
  onTargetChange: (id: string) => void;
  onSubmit: (message: string) => void;
  onStop: () => void;
  running: boolean;
  sessionId: string | null;
}) {
  const [value, setValue] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const comparison = mode === "feature_comparison";

  const upload = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      setUploading((count) => count + list.length);
      for (const file of list) {
        try {
          const asset = await api.uploadFile(file, { sessionId: sessionId ?? undefined });
          onAttach(asset);
          if (!asset.indexed) {
            toast.warning(`${asset.filename} could not be indexed`, {
              description: asset.index_error ?? undefined,
            });
          }
        } catch (error) {
          toast.error(`Could not upload ${file.name}`, {
            description: error instanceof Error ? error.message : undefined,
          });
        } finally {
          setUploading((count) => count - 1);
        }
      }
    },
    [onAttach, sessionId],
  );

  function submit() {
    const message = value.trim();
    if (!message || running) return;
    onSubmit(message);
    setValue("");
  }

  return (
    <div className="px-6 pb-5">
      <div className="mx-auto w-full max-w-3xl space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <ModeSelector value={mode} onChange={onModeChange} />

          {!comparison && templates.length > 0 ? (
            <Select value={templateKey ?? undefined} onValueChange={onTemplateChange}>
              <SelectTrigger className="h-8 w-auto min-w-[190px] gap-2 bg-surface text-2xs">
                <SelectValue placeholder="Choose a template" />
              </SelectTrigger>
              <SelectContent>
                {templates.map((template) => (
                  <SelectItem key={template.key} value={template.key}>
                    <span className="flex flex-col">
                      <span>{template.name}</span>
                      {template.standard ? (
                        <span className="text-2xs text-muted-foreground">{template.standard}</span>
                      ) : null}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}

          {comparison ? (
            <span className="text-2xs text-muted-foreground">
              {targetId
                ? `${attachments.length - 1} source${attachments.length - 1 === 1 ? "" : "s"} against 1 target`
                : "Mark one attachment as the target"}
            </span>
          ) : null}
        </div>

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
          className={cn(
            "rounded-xl border bg-surface shadow-subtle transition-colors duration-150",
            dragging ? "border-brand/60 bg-brand-subtle/40" : "border-border",
          )}
        >
          {attachments.length > 0 || uploading > 0 ? (
            <div className="flex flex-wrap gap-1.5 border-b border-border p-2.5">
              {attachments.map((file) => (
                <AttachmentChip
                  key={file.id}
                  file={file}
                  comparison={comparison}
                  isTarget={comparison && targetId === file.id}
                  onRemove={() => onRemoveAttachment(file.id)}
                  onMakeTarget={() => onTargetChange(file.id)}
                />
              ))}
              {uploading > 0 ? (
                <span className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1 text-2xs text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" />
                  Indexing {uploading} file{uploading === 1 ? "" : "s"}
                </span>
              ) : null}
            </div>
          ) : null}

          <Textarea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            rows={3}
            placeholder={
              comparison
                ? "Check the supplier datasheet against the mission requirements baseline…"
                : "Draft the mission system specification from the attached study report…"
            }
            className="max-h-56 min-h-[76px] resize-none border-0 bg-transparent px-3.5 py-3 text-sm shadow-none focus-visible:ring-0"
          />

          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex items-center gap-1">
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPT}
                multiple
                className="hidden"
                onChange={(event) => {
                  if (event.target.files?.length) void upload(event.target.files);
                  event.target.value = "";
                }}
              />
              <Button variant="ghost" size="sm" onClick={() => inputRef.current?.click()}>
                <Paperclip />
                Attach
              </Button>
              <span className="hidden text-2xs text-muted-foreground sm:inline">
                PDF · PPTX · DOCX · TXT
              </span>
            </div>

            {running ? (
              <Button variant="outline" size="sm" onClick={onStop}>
                <Square className="size-3" />
                Stop
              </Button>
            ) : (
              <Button size="icon" onClick={submit} disabled={!value.trim()} aria-label="Send">
                <ArrowUp />
              </Button>
            )}
          </div>
        </div>

        <p className="text-center text-2xs text-muted-foreground/70">
          Every citation carries an exact page number. Open the source and verify it.
        </p>
      </div>
    </div>
  );
}
