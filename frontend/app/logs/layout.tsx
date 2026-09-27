"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/layout/logo";
import { useAuth } from "@/lib/auth";

/**
 * Deliberately no Sidebar/TopBar here: a logs page is opened in its own tab
 * as a reference view, not a place to navigate from — the (app) chrome would
 * just be dead weight. Still gated the same way (app)/layout.tsx gates
 * everything else, since a signed-out tab must not sit on a blank page
 * waiting for API calls that will only 401.
 */
export default function LogsLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Logo className="size-6 animate-pulse opacity-40" />
      </div>
    );
  }

  return <div className="min-h-screen bg-background">{children}</div>;
}
