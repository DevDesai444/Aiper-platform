/**
 * Pure, dependency-free helpers for the Supabase auth path.
 *
 * Deliberately imports nothing so it can be unit-tested directly under the Node
 * test runner (no bundler, no DOM, no supabase-js). The stateful client lives in
 * `./supabase`; anything with real logic worth testing lives here.
 */

export interface SupabaseEnv {
  url: string | undefined;
  anonKey: string | undefined;
}

/** Supabase owns identity only when BOTH the project URL and anon key are present. */
export function isSupabaseConfigured(env: SupabaseEnv): boolean {
  return Boolean(env.url && env.anonKey);
}

export interface RegisterInput {
  email: string;
  password: string;
  full_name: string;
  organisation: string;
}

export interface SupabaseSignUpArgs {
  email: string;
  password: string;
  options: { data: { full_name: string; organisation: string } };
}

/**
 * Map the register form into supabase-js `signUp` arguments. Full name and
 * organisation are carried as `user_metadata` (the `data` field); the backend's
 * lazy provisioning reads them from the verified JWT on first sight.
 */
export function buildSignUpCredentials(input: RegisterInput): SupabaseSignUpArgs {
  return {
    email: input.email.trim(),
    password: input.password,
    options: {
      data: {
        full_name: input.full_name.trim(),
        organisation: input.organisation.trim(),
      },
    },
  };
}

/**
 * Choose the Bearer token for an API call. When Supabase owns identity we send
 * its (auto-refreshed) access token; otherwise we send the legacy self-issued
 * token. Returns null when there is nothing to send (anonymous request).
 */
export function chooseBearerToken(input: {
  supabaseConfigured: boolean;
  supabaseAccessToken: string | null;
  legacyToken: string | null;
}): string | null {
  return input.supabaseConfigured ? input.supabaseAccessToken : input.legacyToken;
}

/** The `access_token` from a supabase-js session, or null when there is none. */
export interface SessionLike {
  access_token?: string | null;
}

export function extractAccessToken(session: SessionLike | null | undefined): string | null {
  return session?.access_token ?? null;
}
