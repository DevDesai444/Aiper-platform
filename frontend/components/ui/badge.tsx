import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-2xs font-medium [&_svg]:size-3",
  {
    variants: {
      variant: {
        default: "border-border bg-surface-sunken text-muted-foreground",
        solid: "border-transparent bg-primary text-primary-foreground",
        brand: "border-brand/20 bg-brand-subtle text-brand",
        outline: "border-border-strong text-muted-foreground",
        warning: "border-verdict-partial/25 bg-verdict-partial-bg text-verdict-partial",
        success: "border-verdict-pass/25 bg-verdict-pass-bg text-verdict-pass",
        danger: "border-verdict-fail/25 bg-verdict-fail-bg text-verdict-fail",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
