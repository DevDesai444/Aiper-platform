"use client";

import {
  ChevronsUpDown,
  FileStack,
  GitBranch,
  LogOut,
  MessagesSquare,
  Plus,
  Settings,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Wordmark } from "@/components/layout/logo";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth";
import { cn, initials } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

const NAV = [
  { href: "/chat", label: "Chat", icon: MessagesSquare },
  { href: "/editor", label: "Traceability Editor", icon: GitBranch },
  { href: "/vault", label: "Document Vault", icon: FileStack },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, health, signOut } = useAuth();
  const { sessions } = useWorkspace();

  return (
    <aside className="flex w-[248px] shrink-0 flex-col border-r border-border bg-surface-sunken">
      <div className="flex h-14 items-center justify-between px-4">
        <Link href="/chat" className="rounded-md">
          <Wordmark />
        </Link>
        {health?.mock ? (
          <Badge variant="warning" title="Serving the scripted mock backend">
            mock
          </Badge>
        ) : null}
      </div>

      <nav className="space-y-0.5 px-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[0.8125rem] font-medium transition-colors",
                active
                  ? "bg-surface text-foreground shadow-xs"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}
            >
              <Icon className={cn("size-4", active ? "text-brand" : "text-muted-foreground")} />
              <span className="truncate">{label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-6 flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-4 pb-1.5">
          <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
            Conversations
          </span>
          <Button asChild variant="ghost" size="icon-sm" title="New conversation">
            <Link href="/chat">
              <Plus />
            </Link>
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {sessions.length === 0 ? (
            <p className="px-2.5 py-1.5 text-2xs leading-relaxed text-muted-foreground/80">
              Nothing yet. Attach a source and ask for a draft.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {sessions.map((session) => {
                const active = pathname === `/chat/${session.id}`;
                return (
                  <li key={session.id}>
                    <Link
                      href={`/chat/${session.id}`}
                      className={cn(
                        "flex items-center gap-2 rounded-md px-2.5 py-[7px] text-[0.8125rem] transition-colors",
                        active
                          ? "bg-surface text-foreground shadow-xs"
                          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          session.mode === "feature_comparison"
                            ? "bg-verdict-partial"
                            : "bg-brand/70",
                        )}
                      />
                      <span className="truncate">{session.title}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <div className="border-t border-border p-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent/60">
              <Avatar>
                <AvatarFallback>
                  {initials(user?.full_name ?? "", user?.email ?? "a")}
                </AvatarFallback>
              </Avatar>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.8125rem] font-medium">
                  {user?.full_name || user?.email?.split("@")[0] || "Account"}
                </span>
                <span className="block truncate text-2xs text-muted-foreground">
                  {user?.email}
                </span>
              </span>
              <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" side="top" className="w-[228px]">
            <DropdownMenuLabel>{user?.organisation || "Workspace"}</DropdownMenuLabel>
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
    </aside>
  );
}
