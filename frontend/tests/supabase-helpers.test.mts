/**
 * Unit tests for the Supabase auth helpers.
 *
 * Runs on the built-in Node test runner (`node --test`) with no extra tooling —
 * Node 22 strips the TypeScript types on the fly. supabase-js is not imported;
 * its data shapes (session, signUp args) are represented with plain fakes, which
 * is all these pure functions need.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildSignUpCredentials,
  chooseBearerToken,
  extractAccessToken,
  isSupabaseConfigured,
} from "../lib/supabase-helpers.ts";

test("isSupabaseConfigured: true only when url AND anonKey are present", () => {
  assert.equal(isSupabaseConfigured({ url: "https://x.supabase.co", anonKey: "anon" }), true);
  assert.equal(isSupabaseConfigured({ url: "https://x.supabase.co", anonKey: undefined }), false);
  assert.equal(isSupabaseConfigured({ url: undefined, anonKey: "anon" }), false);
  assert.equal(isSupabaseConfigured({ url: undefined, anonKey: undefined }), false);
  // Empty strings are treated as absent (unset build args serialise to "").
  assert.equal(isSupabaseConfigured({ url: "", anonKey: "" }), false);
  assert.equal(isSupabaseConfigured({ url: "https://x.supabase.co", anonKey: "" }), false);
});

test("buildSignUpCredentials: carries full_name + organisation as user_metadata", () => {
  const args = buildSignUpCredentials({
    email: "ada@example.com",
    password: "hunter2hunter2",
    full_name: "Ada Lovelace",
    organisation: "ESA",
  });
  assert.deepEqual(args, {
    email: "ada@example.com",
    password: "hunter2hunter2",
    options: { data: { full_name: "Ada Lovelace", organisation: "ESA" } },
  });
});

test("buildSignUpCredentials: trims email, name and organisation (not password)", () => {
  const args = buildSignUpCredentials({
    email: "  ada@example.com  ",
    password: "  keep spaces  ",
    full_name: "  Ada  ",
    organisation: "  ESA  ",
  });
  assert.equal(args.email, "ada@example.com");
  assert.equal(args.password, "  keep spaces  ");
  assert.equal(args.options.data.full_name, "Ada");
  assert.equal(args.options.data.organisation, "ESA");
});

test("chooseBearerToken: prefers the Supabase token when configured", () => {
  assert.equal(
    chooseBearerToken({
      supabaseConfigured: true,
      supabaseAccessToken: "supabase-jwt",
      legacyToken: "legacy-jwt",
    }),
    "supabase-jwt",
  );
  // Configured but signed out → no token, and the legacy token is NOT used.
  assert.equal(
    chooseBearerToken({
      supabaseConfigured: true,
      supabaseAccessToken: null,
      legacyToken: "legacy-jwt",
    }),
    null,
  );
});

test("chooseBearerToken: falls back to the legacy token when not configured", () => {
  assert.equal(
    chooseBearerToken({
      supabaseConfigured: false,
      supabaseAccessToken: "supabase-jwt",
      legacyToken: "legacy-jwt",
    }),
    "legacy-jwt",
  );
  assert.equal(
    chooseBearerToken({ supabaseConfigured: false, supabaseAccessToken: null, legacyToken: null }),
    null,
  );
});

test("extractAccessToken: reads a session's access_token, null otherwise", () => {
  assert.equal(extractAccessToken({ access_token: "abc" }), "abc");
  assert.equal(extractAccessToken(null), null);
  assert.equal(extractAccessToken(undefined), null);
  assert.equal(extractAccessToken({}), null);
});
