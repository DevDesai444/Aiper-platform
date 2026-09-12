import { cn } from "@/lib/utils";

/**
 * The mark: an orbital path around a solid body. Flat, two-tone, no glow.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      className={cn("size-[18px] text-foreground", className)}
    >
      <circle cx="12" cy="12" r="3.4" fill="currentColor" />
      <ellipse
        cx="12"
        cy="12"
        rx="10"
        ry="5.2"
        transform="rotate(-28 12 12)"
        stroke="currentColor"
        strokeOpacity="0.42"
        strokeWidth="1.4"
      />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <Logo />
      <span className="text-[0.9375rem] font-semibold tracking-[-0.02em]">aiper</span>
    </span>
  );
}
