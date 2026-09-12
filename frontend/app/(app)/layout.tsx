"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/layout/logo";
import { Sidebar } from "@/components/layout/sidebar";
import { useAuth } from "@/lib/auth";
import { WorkspaceProvider } from "@/lib/workspace";

export default function AppLayout({ children }: { children: React.ReactNode }) {
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

  return (
    <WorkspaceProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col bg-background">{children}</main>
      </div>
    </WorkspaceProvider>
  );
}
