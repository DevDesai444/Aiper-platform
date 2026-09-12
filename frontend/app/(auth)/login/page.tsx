"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { AuthCard, AuthField } from "@/components/layout/auth-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { signIn, health } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    try {
      await signIn(email.trim(), password);
    } catch (error) {
      toast.error("Could not sign in", {
        description: error instanceof Error ? error.message : "Unexpected error",
      });
    } finally {
      setPending(false);
    }
  }

  function useDemoAccount() {
    setEmail("demo@aiper.dev");
    setPassword("demo1234");
  }

  return (
    <AuthCard
      title="Sign in"
      description="Continue to your workspace."
      footer={
        <span>
          No account yet?{" "}
          <Link href="/register" className="font-medium text-foreground hover:underline">
            Create one
          </Link>
        </span>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <AuthField label="Work email" htmlFor="email">
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@agency.int"
          />
        </AuthField>

        <AuthField label="Password" htmlFor="password">
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="••••••••"
          />
        </AuthField>

        <Button type="submit" className="w-full" loading={pending}>
          Sign in
        </Button>
      </form>

      {health?.mock ? (
        <button
          type="button"
          onClick={useDemoAccount}
          className="mt-4 w-full rounded-md border border-dashed border-border bg-surface-sunken px-3 py-2 text-left text-2xs text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
        >
          <span className="font-medium text-foreground">Mock backend detected.</span> Use the
          seeded demo account — demo@aiper.dev / demo1234.
        </button>
      ) : null}
    </AuthCard>
  );
}
