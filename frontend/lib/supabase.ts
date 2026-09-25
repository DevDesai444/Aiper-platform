/**
 * Supabase browser client (production identity path).
 *
 * When `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are both
 * baked in at build time, this owns sign-in/up/out and holds the session
 * (persisted to localStorage, auto-refreshed). When either is absent every entry
 * point is a no-op and the app falls back to the legacy self-issued login.
 *
 * These are `NEXT_PUBLIC_*` (not `VITE_*`) because this is a Next.js app: only
 * that prefix is inlined into the client bundle.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { extractAccessToken, isSupabaseConfigured, type SupabaseEnv } from "@/lib/supabase-helpers";

/** Read the build-time env. The literal `process.env.NEXT_PUBLIC_*` member access
 *  is what Next statically replaces, so it must not be dynamically indexed. */
export function readSupabaseEnv(): SupabaseEnv {
  return {
    url: process.env.NEXT_PUBLIC_SUPABASE_URL,
    anonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  };
}

/** True when Supabase is configured and should be the sole auth path. */
export function isSupabaseEnabled(): boolean {
  return isSupabaseConfigured(readSupabaseEnv());
}

// `undefined` = not yet resolved; `null` = resolved-and-disabled.
let client: SupabaseClient | null | undefined;

/** The singleton browser client, or null when Supabase is not configured. */
export function getSupabaseClient(): SupabaseClient | null {
  if (client !== undefined) return client;

  const env = readSupabaseEnv();
  if (!isSupabaseConfigured(env)) {
    client = null;
    return client;
  }

  client = createClient(env.url as string, env.anonKey as string, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      // Namespaced so it never collides with the legacy `aiper.token` key.
      storageKey: "aiper.supabase.auth",
    },
  });
  return client;
}

/**
 * The current Supabase access token, or null. `getSession()` returns the
 * in-memory session and transparently refreshes it when near expiry, so this is
 * always a token the backend will accept (or null if signed out).
 */
export async function getSupabaseAccessToken(): Promise<string | null> {
  const supabase = getSupabaseClient();
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return extractAccessToken(data.session);
}
