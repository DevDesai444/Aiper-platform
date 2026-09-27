/**
 * Unit tests for the slugged-URL helpers.
 *
 * Runs on the built-in Node test runner (`node --test`), same as
 * supabase-helpers.test.mts — no bundler, no DOM, nothing to fake.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { extractRef, idSlug, slugify } from "../lib/slug.ts";

const UUID = "7d0182d2-8a1e-4f3b-9c2a-1234567890ab";
const ID8 = UUID.slice(0, 8); // "7d0182d2"

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

test("idSlug: joins the slug and the first 8 hex characters of the id", () => {
  assert.equal(idSlug("Lunar Lander Avionics", UUID), `lunar-lander-avionics-${ID8}`);
});

test("idSlug: a name that slugifies to nothing produces the bare id8", () => {
  assert.equal(idSlug("", UUID), ID8);
  assert.equal(idSlug("!!!", UUID), ID8);
  assert.equal(idSlug("   ", UUID), ID8);
});

test("extractRef: reads the id8 back out of a slugged segment", () => {
  assert.equal(extractRef(`lunar-lander-avionics-${ID8}`), ID8);
  assert.equal(extractRef(`multi-word-slug-with-many-dashes-${ID8}`), ID8);
});

test("extractRef: a bare id8 is a valid segment on its own", () => {
  assert.equal(extractRef(ID8), ID8);
});

test("extractRef: a bare full uuid resolves exactly — every pre-slug link keeps working", () => {
  assert.equal(extractRef(UUID), UUID);
  assert.equal(extractRef(UUID.toUpperCase()), UUID.toUpperCase());
});

test("extractRef: the full-uuid case wins even inside a slugged segment", () => {
  // Not a shape idSlug ever produces, but a defensive check that the longer,
  // more specific match is preferred when both could apply.
  assert.equal(extractRef(`old-bookmark-${UUID}`), UUID);
});

test("extractRef: null for anything that ends in neither a uuid nor 8 hex characters", () => {
  assert.equal(extractRef(""), null);
  assert.equal(extractRef("not-hex!!"), null);
  assert.equal(extractRef("short"), null); // 5 chars, none of them the id
  // 8 characters, but not hex.
  assert.equal(extractRef("nothexat"), null);
});

test("extractRef: only the trailing characters count, not an id8 earlier in the string", () => {
  // Ends in garbage, so it must be rejected even though a valid id8 appears
  // earlier — the id is never "found somewhere", only at the end.
  assert.equal(extractRef(`${ID8}-not-the-end`), null);
});

test("round-trips through idSlug for any name, including one that slugifies away", () => {
  for (const name of ["Lunar Lander Avionics", "", "!!!", "Ground Segment 2"]) {
    assert.equal(extractRef(idSlug(name, UUID)), ID8, `failed for name ${JSON.stringify(name)}`);
  }
});
