"use client";

import { FileSignature, GitCompareArrows } from "lucide-react";

import type { Mode } from "@/lib/types";
import { cn } from "@/lib/utils";

const MODES = [
  {
    value: "document_generation" as Mode,
    label: "Document Generation",
    icon: FileSignature,
  },
  {
    value: "feature_comparison" as Mode,
    label: "Feature Comparison",
    icon: GitCompareArrows,
  },
];

export function ModeSelector({
  value,
  onChange,
}: {
  value: Mode;
  onChange: (mode: Mode) => void;
}) {
  return (
    <div
      role="tablist"
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-surface-sunken p-0.5"
    >
      {MODES.map(({ value: mode, label, icon: Icon }) => {
        const active = value === mode;
        return (
          <button
            key={mode}
            role="tab"
            aria-selected={active}
            type="button"
            onClick={() => onChange(mode)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-2xs font-medium transition-colors",
              active
                ? "bg-surface text-foreground shadow-xs"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className={cn("size-3.5", active && "text-brand")} />
            {label}
          </button>
        );
      })}
    </div>
  );
}
