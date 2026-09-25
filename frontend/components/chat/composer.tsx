"use client";

import { ArrowUp, Square } from "lucide-react";
import { useState } from "react";

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
import type { DocumentTemplate, Mode } from "@/lib/types";

export function Composer({
  mode,
  onModeChange,
  templates,
  templateKey,
  onTemplateChange,
  onSubmit,
  onStop,
  running,
}: {
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  templates: DocumentTemplate[];
  templateKey: string | null;
  onTemplateChange: (key: string) => void;
  onSubmit: (message: string) => void;
  onStop: () => void;
  running: boolean;
}) {
  const [value, setValue] = useState("");
  const comparison = mode === "feature_comparison";

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
        </div>

        <div className="rounded-xl border border-border bg-surface shadow-subtle">
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
                : "Draft the mission system specification…"
            }
            className="max-h-56 min-h-[76px] resize-none border-0 bg-transparent px-3.5 py-3 text-sm shadow-none focus-visible:ring-0"
          />

          <div className="flex items-center justify-end gap-2 px-2.5 pb-2.5">
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
