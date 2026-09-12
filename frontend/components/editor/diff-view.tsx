"use client";

import { Minus, Plus, RefreshCw } from "lucide-react";

import type { Diff, DiffBlock } from "@/lib/types";
import { cn } from "@/lib/utils";

function Gutter({ value }: { value?: number | null }) {
  return (
    <span className="tabular w-8 shrink-0 select-none pr-2 text-right text-2xs text-muted-foreground/50">
      {value ?? ""}
    </span>
  );
}

function Line({ block }: { block: DiffBlock }) {
  if (block.kind === "collapsed") {
    return (
      <div className="flex items-center gap-3 py-2">
        <span className="h-px flex-1 bg-border" />
        <span className="text-2xs text-muted-foreground/70">
          ··· {block.count} unchanged {block.count === 1 ? "block" : "blocks"} ···
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
    );
  }

  if (block.kind === "equal") {
    return (
      <div className="flex items-baseline">
        <Gutter value={block.new_line} />
        <span className="flex-1 whitespace-pre-wrap break-words text-[0.8125rem] leading-relaxed text-muted-foreground">
          {block.new_text}
        </span>
      </div>
    );
  }

  if (block.kind === "modify") {
    return (
      <div className="space-y-px">
        <div className="flex items-baseline rounded-sm bg-verdict-fail-bg/70">
          <Gutter value={block.old_line} />
          <span className="flex-1 whitespace-pre-wrap break-words pr-2 text-[0.8125rem] leading-relaxed text-verdict-fail line-through decoration-verdict-fail/40">
            {block.old_text}
          </span>
        </div>
        <div className="flex items-baseline rounded-sm bg-verdict-pass-bg/70">
          <Gutter value={block.new_line} />
          <span className="flex-1 whitespace-pre-wrap break-words pr-2 text-[0.8125rem] leading-relaxed text-verdict-pass">
            {block.new_text}
          </span>
        </div>
      </div>
    );
  }

  const added = block.kind === "add";
  return (
    <div
      className={cn(
        "flex items-baseline rounded-sm",
        added ? "bg-verdict-pass-bg/70" : "bg-verdict-fail-bg/70",
      )}
    >
      <Gutter value={added ? block.new_line : block.old_line} />
      <span
        className={cn(
          "flex-1 whitespace-pre-wrap break-words pr-2 text-[0.8125rem] leading-relaxed",
          added ? "text-verdict-pass" : "text-verdict-fail line-through decoration-verdict-fail/40",
        )}
      >
        {added ? block.new_text : block.old_text}
      </span>
    </div>
  );
}

export function DiffStat({
  additions,
  deletions,
  modifications,
  className,
}: {
  additions: number;
  deletions: number;
  modifications: number;
  className?: string;
}) {
  return (
    <span className={cn("tabular inline-flex items-center gap-2 text-2xs", className)}>
      <span className="inline-flex items-center gap-0.5 text-verdict-pass">
        <Plus className="size-2.5" />
        {additions}
      </span>
      <span className="inline-flex items-center gap-0.5 text-verdict-fail">
        <Minus className="size-2.5" />
        {deletions}
      </span>
      <span className="inline-flex items-center gap-0.5 text-verdict-partial">
        <RefreshCw className="size-2.5" />
        {modifications}
      </span>
    </span>
  );
}

export function DiffView({ diff }: { diff: Diff }) {
  if (diff.blocks.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-2xs text-muted-foreground">
        This revision made no change to the document body.
      </p>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
          Changes
        </span>
        <DiffStat
          additions={diff.additions}
          deletions={diff.deletions}
          modifications={diff.modifications}
        />
      </div>
      <div className="max-h-[56vh] space-y-px overflow-y-auto p-2 font-sans">
        {diff.blocks.map((block, index) => (
          <Line key={index} block={block} />
        ))}
      </div>
    </div>
  );
}
