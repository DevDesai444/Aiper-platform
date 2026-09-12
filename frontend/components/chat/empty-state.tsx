"use client";

import { Crosshair, FileSignature, GitCompareArrows, Paperclip } from "lucide-react";

import { Logo } from "@/components/layout/logo";
import type { Mode } from "@/lib/types";

const GUIDES: Record<Mode, { icon: typeof FileSignature; title: string; steps: string[] }> = {
  document_generation: {
    icon: FileSignature,
    title: "Document Generation",
    steps: [
      "Attach the study reports, notes or datasheets the deliverable should be built from.",
      "Pick the template — ECSS mission spec, SRS, ICD, RFP response, compliance matrix or technical note.",
      "Ask for the draft. Each section is retrieved, cited and assembled, and gaps are marked [TBC] rather than invented.",
    ],
  },
  feature_comparison: {
    icon: GitCompareArrows,
    title: "Feature Comparison",
    steps: [
      "Attach the baseline you are checking against and mark it as the target.",
      "Attach the documents to check — supplier datasheets, offers, design notes.",
      "Ask for the matrix. Every item gets one verdict and a page citation you can open and verify.",
    ],
  },
};

export function EmptyState({ mode }: { mode: Mode }) {
  const guide = GUIDES[mode];

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center px-6 py-16 text-center animate-fade-in">
      <div className="flex size-11 items-center justify-center rounded-xl border border-border bg-surface shadow-xs">
        <Logo className="size-5" />
      </div>

      <h2 className="mt-5 text-[1.0625rem] font-semibold tracking-[-0.012em]">
        {guide.title}
      </h2>
      <p className="mt-1.5 max-w-md text-balance text-[0.8125rem] leading-relaxed text-muted-foreground">
        Evidence first, page-exact citations, nothing invented.
      </p>

      <ol className="mt-8 w-full space-y-3 text-left">
        {guide.steps.map((step, index) => (
          <li
            key={index}
            className="flex gap-3 rounded-lg border border-border bg-surface p-3.5 shadow-xs"
          >
            <span className="tabular mt-px flex size-5 shrink-0 items-center justify-center rounded-md border border-border bg-surface-sunken text-2xs font-medium text-muted-foreground">
              {index + 1}
            </span>
            <span className="text-[0.8125rem] leading-relaxed text-foreground/85">{step}</span>
          </li>
        ))}
      </ol>

      <div className="mt-7 flex items-center gap-4 text-2xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <Paperclip className="size-3" />
          Drag files onto the composer
        </span>
        {mode === "feature_comparison" ? (
          <span className="inline-flex items-center gap-1.5">
            <Crosshair className="size-3" />
            One target, N sources
          </span>
        ) : null}
      </div>
    </div>
  );
}
