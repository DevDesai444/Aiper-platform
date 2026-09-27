"use client";

import { FolderKanban, MessagesSquare, Plus, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

const NAV = [
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/chat", label: "Chat", icon: MessagesSquare },
  { href: "/settings", label: "Settings", icon: Settings },
];

/**
 * Navigation and the conversation list. The brand mark and the account menu
 * live in the app-wide top bar now — this is nav only, no duplicate identity.
 */
export function Sidebar() {
  const pathname = usePathname();
  const { sessions } = useWorkspace();

  return (
    <aside className="flex w-[248px] shrink-0 flex-col border-r border-border bg-surface-sunken">
      <nav className="space-y-0.5 px-2 pt-3">
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
          <Link
            href="/chat"
            title="New conversation"
            className="flex size-7 shrink-0 items-center justify-center rounded transition-colors hover:bg-accent"
          >
            <Plus className="size-4 text-muted-foreground" />
          </Link>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {sessions.length === 0 ? (
            <p className="px-2.5 py-1.5 text-2xs leading-relaxed text-muted-foreground/80">
              Nothing yet. Ask for a draft and it will appear here.
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
    </aside>
  );
}
