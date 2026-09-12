"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/layout/logo";
import { getToken } from "@/lib/api";

export default function IndexPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getToken() ? "/chat" : "/login");
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Logo className="size-6 animate-pulse opacity-40" />
    </div>
  );
}
