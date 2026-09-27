"use client";

import { LogOut, Settings } from "lucide-react";
import Link from "next/link";

import { Wordmark } from "@/components/layout/logo";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";

/**
 * App-wide top chrome: the brand mark, and the account it belongs to. Spans
 * the full width above the sidebar and the content, the same shape Drive and
 * Gmail use — the sidebar underneath it stops needing its own identity block.
 */
export function TopBar() {
  const { user, health, signOut } = useAuth();

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface-sunken px-4">
      <Link href="/projects" className="rounded-md">
        <Wordmark />
      </Link>

      <div className="flex items-center gap-2">
        {health?.mock ? (
          <Badge variant="warning" title="Serving the scripted mock backend">
            mock
          </Badge>
        ) : null}
        <ThemeToggle />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2.5 rounded-md py-1.5 pl-2.5 pr-1.5 text-left transition-colors hover:bg-accent/60">
              <span className="hidden min-w-0 sm:block">
                <span className="block max-w-[160px] truncate text-[0.8125rem] font-medium">
                  {user?.full_name || user?.email?.split("@")[0] || "Account"}
                </span>
                <span className="block max-w-[160px] truncate text-2xs text-muted-foreground">
                  {user?.organisation || "Workspace"}
                </span>
              </span>
              <Avatar>
                <AvatarFallback>{initials(user?.full_name ?? "", user?.email ?? "a")}</AvatarFallback>
              </Avatar>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[228px]">
            <DropdownMenuLabel className="truncate">{user?.email}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/settings">
                <Settings />
                Settings
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem destructive onSelect={signOut}>
              <LogOut />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
