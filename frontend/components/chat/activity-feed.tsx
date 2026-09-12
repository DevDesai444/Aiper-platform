"use client";

import {
  AlertCircle,
  BookOpen,
  Check,
  ChevronRight,
  CircleDashed,
  ListChecks,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react";
import { useState } from "react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ActivityRow } from "@/lib/types";
import { cn } from "@/lib/utils";

function RowIcon({ row }: { row: ActivityRow }) {
  if (row.running) return <Loader2 className="size-3.5 animate-spin text-brand" />;
  if (row.kind === "error") return <AlertCircle className="size-3.5 text-destructive" />;
  return <Check className="size-3.5 text-verdict-pass" />;
}

function KindIcon({ kind }: { kind: ActivityRow["kind"] }) {
  const className = "size-3.5 text-muted-foreground";
  if (kind === "plan") return <ListChecks className={className} />;
  if (kind === "skill") return <BookOpen className={className} />;
  if (kind === "delegation") return <Sparkles className={className} />;
  if (kind === "error") return <AlertCircle className="size-3.5 text-destructive" />;
  return <Search className={className} />;
}

function PlanChecklist({ row }: { row: ActivityRow }) {
  return (
    <ol className="space-y-1.5">
      {row.items?.map((item, index) => (
        <li key={index} className="flex items-start gap-2">
          {item.status === "completed" ? (
            <Check className="mt-[3px] size-3 shrink-0 text-verdict-pass" />
          ) : item.status === "in_progress" ? (
            <Loader2 className="mt-[3px] size-3 shrink-0 animate-spin text-brand" />
          ) : (
            <CircleDashed className="mt-[3px] size-3 shrink-0 text-muted-foreground/60" />
          )}
          <span
            className={cn(
              "text-[0.8125rem] leading-snug",
              item.status === "completed"
                ? "text-muted-foreground"
                : item.status === "in_progress"
                  ? "font-medium text-foreground"
                  : "text-muted-foreground/80",
            )}
          >
            {item.content}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Block({ title, text, muted }: { title: string; text: string; muted?: boolean }) {
  return (
    <div>
      <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <pre
        className={cn(
          "whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed",
          muted ? "text-muted-foreground" : "text-foreground/75",
        )}
      >
        {text}
      </pre>
    </div>
  );
}

function Row({ row }: { row: ActivityRow }) {
  const [open, setOpen] = useState(false);
  const children = row.children ?? [];
  const expandable = Boolean(row.items?.length || row.detail || row.output || children.length);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        disabled={!expandable}
        className={cn(
          "group flex w-full items-center gap-2 rounded-md px-2 py-[5px] text-left transition-colors",
          expandable && "hover:bg-accent/60",
        )}
      >
        <RowIcon row={row} />
        <KindIcon kind={row.kind} />
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-[0.8125rem]",
            row.kind === "error" ? "text-destructive" : "text-foreground/85",
          )}
        >
          {row.kind === "plan" && row.items
            ? `Plan · ${row.items.filter((i) => i.status === "completed").length}/${row.items.length} steps`
            : row.label}
          {row.agent ? (
            <code className="ml-1.5 rounded border border-border bg-surface-sunken px-1 py-px font-mono text-2xs text-muted-foreground">
              {row.agent}
            </code>
          ) : null}
        </span>
        {children.length > 0 ? (
          <span className="tabular shrink-0 text-2xs text-muted-foreground">
            {children.length} {children.length === 1 ? "call" : "calls"}
          </span>
        ) : null}
        {expandable ? (
          <ChevronRight
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground/60 transition-transform duration-150",
              open && "rotate-90",
            )}
          />
        ) : null}
      </CollapsibleTrigger>

      <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-slide-up data-[state=open]:animate-slide-down">
        <div className="ml-[26px] mr-2 mt-1 space-y-2 border-l border-border pb-1.5 pl-3">
          {row.items?.length ? <PlanChecklist row={row} /> : null}
          {row.detail ? <Block title={row.kind === "delegation" ? "Task" : "Input"} text={row.detail} muted /> : null}
          {children.length > 0 ? (
            <div className="-ml-3 space-y-px">
              {children.map((child) => (
                <Row key={child.id} row={child} />
              ))}
            </div>
          ) : null}
          {row.output ? <Block title="Result" text={row.output} /> : null}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ActivityFeed({
  rows,
  running,
  defaultOpen = false,
}: {
  rows: ActivityRow[];
  running: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || running);
  if (rows.length === 0) return null;

  const done = rows.filter((row) => !row.running).length;

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="rounded-lg border border-border bg-surface-sunken/60"
    >
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-90",
          )}
        />
        <span className="text-[0.8125rem] font-medium">Agent activity</span>
        <span className="flex-1" />
        {running ? (
          <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin text-brand" />
            working
          </span>
        ) : (
          <span className="tabular text-2xs text-muted-foreground">
            {done} {done === 1 ? "step" : "steps"}
          </span>
        )}
      </CollapsibleTrigger>

      <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-slide-up data-[state=open]:animate-slide-down">
        <div className="space-y-px border-t border-border px-1.5 py-1.5">
          {rows.map((row) => (
            <Row key={row.id} row={row} />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
