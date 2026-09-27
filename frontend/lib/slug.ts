/**
 * Human-readable URLs: `{slug}-{uuid}` path segments.
 *
 * The slug is pure cosmetics; the trailing UUID is the only thing that is ever
 * authoritative. A bare UUID is a valid segment with an empty slug, so every
 * link handed out before this existed keeps resolving unchanged.
 *
 * Dependency-free like `./supabase-helpers`, so it can be unit-tested directly
 * under the Node test runner.
 */

const MAX_SLUG_LENGTH = 60;
const UUID_LENGTH = 36;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Lowercase, runs of non-alphanumerics collapsed to a single dash, trimmed,
 * capped at 60 characters. May be empty (a blank or all-punctuation name).
 */
export function slugify(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  // The cap can land mid-word (fine) or right after a dash (not fine) if the
  // cut point falls inside what would have been a collapsed separator.
  return slug.slice(0, MAX_SLUG_LENGTH).replace(/-+$/, "");
}

/** `{slug}-{id}`, or the bare id when the name slugifies to nothing. */
export function idSlug(name: string, id: string): string {
  const slug = slugify(name);
  return slug ? `${slug}-${id}` : id;
}

/**
 * The UUID a path segment ends with, or null if it does not end with one.
 *
 * The id is always the last 36 characters of the segment — not "a uuid found
 * somewhere in it" — so a slug that happens to contain 36 hex-and-dash-looking
 * characters earlier in the string is not mistaken for the id.
 */
export function extractId(segment: string): string | null {
  if (segment.length < UUID_LENGTH) return null;
  const tail = segment.slice(-UUID_LENGTH);
  return UUID_PATTERN.test(tail) ? tail : null;
}
