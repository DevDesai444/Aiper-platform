"use client";

import { FileSignature, GitCompareArrows } from "lucide-react";

import type { Mode } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ModeOption {
  value: Mode;
  label: string;
  icon: typeof FileSignature;
  /** Kept in the list but not offered — see below. */
  hidden?: boolean;
}

const MODES: ModeOption[] = [
  {
    value: "document_generation" as Mode,
    label: "Document Generation",
    icon: FileSignature,
  },
  {
    value: "feature_comparison" as Mode,
    label: "Feature Comparison",
    icon: GitCompareArrows,
    // Not offered: the flow it was built around — attach a baseline, mark it
    // as the target — went with the uploads. The mode itself is intact on both
    // sides of the wire and returns once it works over project documents.
    // Until then it is still rendered when a conversation is already in it, so
    // an existing comparison thread does not open with nothing selected.
    hidden: true,
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
      {MODES.filter((mode) => !mode.hidden || value === mode.value).map(
        ({ value: mode, label, icon: Icon }) => {
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
        },
      )}
    </div>
  );
}
