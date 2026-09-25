"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, getToken, setToken } from "@/lib/api";
import { getSupabaseClient, isSupabaseEnabled } from "@/lib/supabase";
import { buildSignUpCredentials } from "@/lib/supabase-helpers";
import type { Health, User } from "@/lib/types";

export interface SignUpInput {
  email: string;
  password: string;
  full_name: string;
  organisation: string;
}

export interface SignUpResult {
  /** Supabase requires email confirmation before a session exists. */
  needsEmailConfirmation: boolean;
}

interface AuthState {
  user: User | null;
  health: Health | null;
  loading: boolean;
  /** Which identity path is active — useful for copy and diagnostics. */
  supabaseAuth: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: SignUpInput) => Promise<SignUpResult>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const supabaseAuth = isSupabaseEnabled();

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Session bootstrap. On the Supabase path the auth-state listener is the single
  // source of truth (initial session, sign-in/out, cross-tab, token refresh). On
  // the legacy path we probe the stored token once.
  useEffect(() => {
    if (!supabaseAuth) {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      api.me().then(setUser).catch(() => setToken(null)).finally(() => setLoading(false));
      return;
    }

    const supabase = getSupabaseClient();
    if (!supabase) {
      setLoading(false);
      return;
    }

    let active = true;
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      if (!session) {
        setUser(null);
        setLoading(false);
        return;
      }
      // Defer: awaiting any supabase call (api.me → getSession) directly inside the
      // callback can deadlock the auth lock, so hop off the callback first.
      setTimeout(() => {
        if (!active) return;
        api
          .me()
          .then((loaded) => active && setUser(loaded))
          .catch(() => active && setUser(null))
          .finally(() => active && setLoading(false));
      }, 0);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [supabaseAuth]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (supabaseAuth) {
        const supabase = getSupabaseClient();
        if (!supabase) throw new Error("Supabase is not configured");
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw new Error(error.message);
        setUser(await api.me());
        router.push("/projects");
        return;
      }
      const result = await api.login({ email, password });
      setToken(result.access_token);
      setUser(result.user);
      router.push("/projects");
    },
    [router, supabaseAuth],
  );

  const signUp = useCallback(
    async (input: SignUpInput): Promise<SignUpResult> => {
      if (supabaseAuth) {
        const supabase = getSupabaseClient();
        if (!supabase) throw new Error("Supabase is not configured");
        const { data, error } = await supabase.auth.signUp(buildSignUpCredentials(input));
        if (error) throw new Error(error.message);
        if (!data.session) {
          // Confirmations are on: no session yet. Caller prompts the user to verify.
          return { needsEmailConfirmation: true };
        }
        setUser(await api.me());
        router.push("/projects");
        return { needsEmailConfirmation: false };
      }
      const result = await api.register(input);
      setToken(result.access_token);
      setUser(result.user);
      router.push("/projects");
      return { needsEmailConfirmation: false };
    },
    [router, supabaseAuth],
  );

  const signOut = useCallback(async () => {
    if (supabaseAuth) {
      await getSupabaseClient()?.auth.signOut();
    } else {
      setToken(null);
    }
    setUser(null);
    router.push("/login");
  }, [router, supabaseAuth]);

  const value = useMemo(
    () => ({ user, health, loading, supabaseAuth, signIn, signUp, signOut }),
    [user, health, loading, supabaseAuth, signIn, signUp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
