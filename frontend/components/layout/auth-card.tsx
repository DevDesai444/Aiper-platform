import type { ReactNode } from "react";

import { Label } from "@/components/ui/label";

export function AuthCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div className="animate-fade-in">
      <div className="rounded-xl border border-border bg-surface p-7 shadow-raised">
        <h1 className="text-[1.25rem] font-semibold tracking-[-0.02em]">{title}</h1>
        <p className="mt-1 text-[0.8125rem] text-muted-foreground">{description}</p>
        <div className="mt-6">{children}</div>
      </div>
      <p className="mt-5 text-center text-[0.8125rem] text-muted-foreground">{footer}</p>
    </div>
  );
}

export function AuthField({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <Label htmlFor={htmlFor}>{label}</Label>
        {hint ? <span className="text-2xs text-muted-foreground">{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}
