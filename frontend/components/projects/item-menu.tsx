"use client";

import { MoreHorizontal } from "lucide-react";
import type { ComponentType, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export interface MenuAction {
  key: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  onSelect: () => void;
  destructive?: boolean;
  disabled?: boolean;
}

/** The shape both DropdownMenuItem and ContextMenuItem actually share, for one map. */
type MenuItemComponent = ComponentType<{
  destructive?: boolean;
  disabled?: boolean;
  onSelect?: (event: Event) => void;
  className?: string;
  children?: ReactNode;
}>;

function renderActions(actions: MenuAction[], Item: MenuItemComponent) {
  return actions.map(({ key, label, icon: Icon, onSelect, destructive, disabled }) => (
    <Item key={key} destructive={destructive} disabled={disabled} onSelect={onSelect}>
      <Icon className="size-3.5" />
      {label}
    </Item>
  ));
}

/**
 * One action list, two entry points: a kebab for discoverability and touch,
 * a right-click for the power path — same items, same handlers, wired once.
 *
 * Wraps `children` (a folder card or document row) with both. The kebab sits
 * on top of it as an always-present but only-on-hover-visible button; the
 * whole wrapped area answers a right-click. `children`'s own click handler
 * (opening the item) is untouched — the kebab button stops its own click
 * from bubbling into it, and Radix's context menu trigger does not consume
 * left-clicks at all.
 */
export function ItemMenu({
  actions,
  kebabLabel,
  className,
  children,
}: {
  actions: MenuAction[];
  kebabLabel: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div className={cn("group/item relative", className)}>
          {children}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={kebabLabel}
                onClick={(event) => event.stopPropagation()}
                className="absolute right-1.5 top-1.5 opacity-0 transition-opacity group-hover/item:opacity-100 data-[state=open]:opacity-100"
              >
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
              {renderActions(actions, DropdownMenuItem)}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent>{renderActions(actions, ContextMenuItem)}</ContextMenuContent>
    </ContextMenu>
  );
}
