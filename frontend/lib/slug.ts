/**
 * Human-readable URLs: `{slug}-{id8}` path segments.
 *
 * The slug is pure cosmetics; an 8-hex-character id is the only thing that is
 * ever authoritative, and even that is resolved server-side by prefix (see
 * `app.services.refs.resolve_ref` on the backend) — never trusted as a full
 * uuid on its own. A bare full uuid is *also* a valid segment (every link
 * minted before any slugging existed), so every old bookmark and every link
 * already sitting in chat history keeps resolving unchanged.
 *
 * Dependency-free like `./supabase-helpers`, so it can be unit-tested directly
 * under the Node test runner.
 */

const MAX_SLUG_LENGTH = 60;
const ID8_LENGTH = 8;
const ID8_PATTERN = /^[0-9a-f]{8}$/i;
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

/** `{slug}-{id8}`, or the bare id8 when the name slugifies to nothing. */
export function idSlug(name: string, id: string): string {
  const id8 = id.slice(0, ID8_LENGTH);
  const slug = slugify(name);
  return slug ? `${slug}-${id8}` : id8;
}

/**
 * The id a path segment ends with — a full uuid or an 8-hex prefix — or null
 * if it ends with neither.
 *
 * The full-uuid case is checked first and takes priority: it is what every
 * link handed out before this scheme existed looks like, and (unlike the
 * 8-hex case) resolves to a real row with no backend lookup at all. Only once
 * that fails is the trailing 8 characters tried as the short id this scheme
 * actually mints — never "a short id found somewhere in the segment," always
 * the last 8 characters specifically, so a slug that happens to end in
 * something hex-looking is not mistaken for it.
 */
export function extractRef(segment: string): string | null {
  if (segment.length >= UUID_LENGTH) {
    const tail = segment.slice(-UUID_LENGTH);
    if (UUID_PATTERN.test(tail)) return tail;
  }
  if (segment.length >= ID8_LENGTH) {
    const tail = segment.slice(-ID8_LENGTH);
    if (ID8_PATTERN.test(tail)) return tail;
  }
  return null;
}
