"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { AuthCard, AuthField } from "@/components/layout/auth-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const { signUp } = useAuth();
  const [form, setForm] = useState({
    full_name: "",
    organisation: "",
    email: "",
    password: "",
  });
  const [pending, setPending] = useState(false);

  function update(key: keyof typeof form) {
    return (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((previous) => ({ ...previous, [key]: event.target.value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (form.password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    setPending(true);
    try {
      await signUp({ ...form, email: form.email.trim() });
    } catch (error) {
      toast.error("Could not create the account", {
        description: error instanceof Error ? error.message : "Unexpected error",
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard
      title="Create an account"
      description="Documents shared with your address are connected automatically."
      footer={
        <span>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-foreground hover:underline">
            Sign in
          </Link>
        </span>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <AuthField label="Full name" htmlFor="full_name">
            <Input id="full_name" value={form.full_name} onChange={update("full_name")} placeholder="Ada Lovelace" />
          </AuthField>
          <AuthField label="Organisation" htmlFor="organisation">
            <Input
              id="organisation"
              value={form.organisation}
              onChange={update("organisation")}
              placeholder="ESA"
            />
          </AuthField>
        </div>

        <AuthField label="Work email" htmlFor="email">
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={form.email}
            onChange={update("email")}
            placeholder="you@agency.int"
          />
        </AuthField>

        <AuthField label="Password" htmlFor="password" hint="At least 8 characters">
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            value={form.password}
            onChange={update("password")}
            placeholder="••••••••"
          />
        </AuthField>

        <Button type="submit" className="w-full" loading={pending}>
          Create account
        </Button>
      </form>
    </AuthCard>
  );
}
