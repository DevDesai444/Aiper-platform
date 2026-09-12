"use client";

import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Route error boundary. Shows the real message so a failure can be reported
 * verbatim instead of hiding behind the framework's generic page.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="w-full max-w-lg rounded-xl border border-border bg-surface p-7 shadow-raised">
        <h1 className="text-[1.0625rem] font-semibold tracking-[-0.012em]">
          Something went wrong on this page
        </h1>
        <p className="mt-1.5 text-[0.8125rem] text-muted-foreground">
          The error below is the exact one the browser raised.
        </p>
        <pre className="mt-4 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-surface-sunken p-3 font-mono text-2xs leading-relaxed text-foreground/85">
          {error.message || String(error)}
          {error.digest ? `\n\ndigest: ${error.digest}` : ""}
          {error.stack ? `\n\n${error.stack}` : ""}
        </pre>
        <div className="mt-5 flex justify-end">
          <Button size="sm" onClick={reset}>
            <RotateCcw />
            Try again
          </Button>
        </div>
      </div>
    </div>
  );
}
