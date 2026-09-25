/**
 * DOCX → HTML for editor import (ported from v1's `editor/docxImport.ts`).
 *
 * Wraps `mammoth`. Mammoth walks the .docx and emits HTML that TipTap's
 * parser turns into StarterKit nodes/marks on `editor.commands.setContent`.
 * We surface any messages mammoth produces (unhandled styles, broken
 * references, etc.) so the caller can hint at fidelity loss without our
 * having to interpret them.
 *
 * Fidelity (MVP)
 *   • Paragraphs, headings, bullet/ordered lists, bold/italic/strike,
 *     hyperlinks — round-trip through mammoth's default style map.
 *   • Tables come through as HTML `<table>`; v2's StarterKit + extension-
 *     table extension handles them.
 *   • Images become `<img>` tags with a data-URI src. StarterKit's default
 *     schema doesn't include an image node, so they're dropped — the text
 *     around them is preserved.
 *   • Footnotes / endnotes / track-changes / comments are dropped by
 *     mammoth; a message per drop appears in the returned warnings.
 *
 * The import lands in the CURRENT document (writes into the editor via
 * `editor.commands.setContent`), so the caller MUST confirm-before-replace
 * when the current doc is non-empty. See `document-editor.tsx` for the
 * confirm wire-up.
 */

/**
 * Mammoth's public API is a JS module without shipped .d.ts types. We keep a
 * narrow local shape for the surface we call rather than pulling in
 * `@types/mammoth` (community-maintained, has drifted from the current API).
 */
interface MammothMessage {
  type?: string;
  message?: string;
}
interface MammothResult {
  value?: string;
  messages?: MammothMessage[];
}
interface MammothModule {
  convertToHtml(input: { arrayBuffer: ArrayBuffer }): Promise<MammothResult>;
}

export interface DocxImportResult {
  /** HTML mammoth produced. Safe to hand to `editor.commands.setContent`. */
  html: string;
  /** One line per mammoth message (missing style, dropped construct, …). */
  warnings: string[];
}

/**
 * Convert a .docx source to HTML for TipTap.
 *
 * `source` may be a `File`, `Blob`, or raw `ArrayBuffer`. Anything with an
 * `.arrayBuffer()` method works.
 *
 * Mammoth is loaded via a dynamic import so its ~1 MB source is not in the
 * initial bundle — a viewer who never imports never pays for the download.
 */
export async function convertDocxToHtml(
  source: File | Blob | ArrayBuffer,
): Promise<DocxImportResult> {
  const arrayBuffer = await toArrayBuffer(source);

  const mod = await loadMammoth();
  const result = await mod.convertToHtml({ arrayBuffer });

  const html = typeof result.value === "string" ? result.value : "";
  const warnings = Array.isArray(result.messages)
    ? result.messages
        .map((m) =>
          typeof m?.message === "string" && m.message.trim().length > 0
            ? m.message
            : null,
        )
        .filter((s): s is string => s !== null)
    : [];

  return { html, warnings };
}

/**
 * Coerce a File / Blob / ArrayBuffer to an ArrayBuffer without assuming
 * `Blob.prototype.arrayBuffer` is defined — some older Safari builds don't
 * expose it, and falling through to a `FileReader` keeps us importable in
 * those envs without polyfilling globals.
 */
function toArrayBuffer(source: File | Blob | ArrayBuffer): Promise<ArrayBuffer> {
  if (source instanceof ArrayBuffer) return Promise.resolve(source);
  const anyBlob = source as { arrayBuffer?: () => Promise<ArrayBuffer> };
  if (typeof anyBlob.arrayBuffer === "function") {
    return anyBlob.arrayBuffer();
  }
  return new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () =>
      reject(reader.error ?? new Error("FileReader failed to read blob"));
    reader.readAsArrayBuffer(source as Blob);
  });
}

/**
 * Testing seam. Real code path is `await import('mammoth')`; tests override
 * via `__setMammothLoader` so they don't have to depend on the real mammoth
 * runtime (which pulls in ~1 MB of dependencies).
 */
type MammothLoader = () => Promise<MammothModule>;
let loader: MammothLoader = () =>
  import("mammoth") as unknown as Promise<MammothModule>;

async function loadMammoth(): Promise<MammothModule> {
  return loader();
}

/**
 * TEST-ONLY. Replace the mammoth loader used by `convertDocxToHtml`. Not
 * part of the public production surface — the underscore prefix makes the
 * "please don't call this from app code" contract obvious.
 */
export function __setMammothLoader(next: MammothLoader | null): void {
  loader = next ?? (() => import("mammoth") as unknown as Promise<MammothModule>);
}
