import Image from "next/image";

import { cn } from "@/lib/utils";

/**
 * The mark: the four-point star from the Aiper Space brand, in brand blue.
 * Used where a compact square mark is needed (chat avatars, editor chrome).
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden
      className={cn("size-[18px] text-[#2447f5]", className)}
    >
      <path
        d="M12 2c1.05 5.9 3.35 8.3 10 10-6.65 1.7-8.95 4.1-10 10-1.05-5.9-3.35-8.3-10-10 6.65-1.7 8.95-4.1 10-10Z"
        fill="currentColor"
      />
    </svg>
  );
}

/**
 * The brand wordmark: the original Aiper Space logo. The source PNG has a
 * baked-in white background, so it sits on a white chip that reads correctly
 * on both themes.
 */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-lg bg-white px-2.5 py-1.5 shadow-sm",
        className,
      )}
    >
      <Image
        src="/aiper-logo.png"
        alt="Aiper Space"
        width={500}
        height={500}
        priority
        className="h-9 w-auto"
      />
    </span>
  );
}
