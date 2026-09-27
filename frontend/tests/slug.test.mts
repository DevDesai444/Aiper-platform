/**
 * Unit tests for the slugged-URL helpers.
 *
 * Runs on the built-in Node test runner (`node --test`), same as
 * supabase-helpers.test.mts — no bundler, no DOM, nothing to fake.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { extractId, idSlug, slugify } from "../lib/slug.ts";

const UUID = "7d0182d2-8a1e-4f3b-9c2a-1234567890ab";

test("slugify: lowercases and collapses whitespace to single dashes", () => {
  assert.equal(slugify("Lunar Lander Avionics"), "lunar-lander-avionics");
  assert.equal(slugify("already-a-slug"), "already-a-slug");
});

test("slugify: collapses runs of punctuation to one dash and trims the ends", () => {
  assert.equal(slugify("C++ Runtime (v2.1)!!!"), "c-runtime-v2-1");
  assert.equal(slugify("  --Hello World--  "), "hello-world");
});

test("slugify: a blank or all-punctuation name slugifies to empty", () => {
  assert.equal(slugify(""), "");
  assert.equal(slugify("   "), "");
  assert.equal(slugify("!!!"), "");
  assert.equal(slugify("---"), "");
});

test("slugify: caps at 60 characters without leaving a trailing dash", () => {
  const long = "word ".repeat(30); // 150 chars of "word word word …"
  const slug = slugify(long);
  assert.ok(slug.length <= 60, `expected <= 60 chars, got ${slug.length}`);
  assert.ok(!slug.endsWith("-"), `expected no trailing dash, got ${JSON.stringify(slug)}`);
});

test("idSlug: joins the slug and the id with one dash", () => {
  assert.equal(idSlug("Lunar Lander Avionics", UUID), `lunar-lander-avionics-${UUID}`);
});

test("idSlug: a name that slugifies to nothing produces the bare id", () => {
  assert.equal(idSlug("", UUID), UUID);
  assert.equal(idSlug("!!!", UUID), UUID);
  assert.equal(idSlug("   ", UUID), UUID);
});

test("extractId: reads the id back out of a slugged segment", () => {
  assert.equal(extractId(`lunar-lander-avionics-${UUID}`), UUID);
  assert.equal(extractId(`multi-word-slug-with-many-dashes-${UUID}`), UUID);
});

test("extractId: a bare uuid is a valid segment — every existing link keeps working", () => {
  assert.equal(extractId(UUID), UUID);
});

test("extractId: matches case-insensitively but returns the id as found", () => {
  const upper = UUID.toUpperCase();
  assert.equal(extractId(upper), upper);
  assert.equal(extractId(`slug-${upper}`), upper);
});

test("extractId: null for anything that does not end in a real uuid", () => {
  assert.equal(extractId(""), null);
  assert.equal(extractId("not-a-uuid"), null);
  assert.equal(extractId("too-short-1234"), null);
  // 36 characters, but not uuid-shaped (no dashes in the right places).
  assert.equal(extractId("x".repeat(36)), null);
  // One character short of a real uuid.
  assert.equal(extractId(UUID.slice(1)), null);
});

test("extractId: only the trailing 36 characters count, not a uuid earlier in the string", () => {
  // The segment ends in garbage, so it must be rejected even though a valid
  // uuid appears earlier — the id is never "found somewhere", only at the end.
  assert.equal(extractId(`${UUID}-not-the-end`), null);
});

test("round-trips through idSlug for any name, including one that slugifies away", () => {
  for (const name of ["Lunar Lander Avionics", "", "!!!", "Ground Segment 2"]) {
    assert.equal(extractId(idSlug(name, UUID)), UUID, `failed for name ${JSON.stringify(name)}`);
  }
});
