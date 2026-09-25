/**
 * ProseMirror JSON → .docx (ported from v1's `editor/docxExport.ts`).
 *
 * Source of truth is `editor.getJSON()`. The tree matches TipTap's schema
 * exactly and is trivial to walk.
 *
 * Mapping ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
 *
 *   Block nodes
 *     paragraph      → Paragraph
 *     heading L1-3   → Paragraph({ heading: HeadingLevel.HEADING_N })
 *     bulletList     → Paragraph(s) with numbering ref BULLET at current
 *                      nesting level
 *     orderedList    → same, ordered numbering ref
 *     listItem       → transparent; children walked with the current list
 *                      context
 *     blockquote     → Paragraph(s) with left indent (720 twip per nesting
 *                      level). Nested blockquotes stack.
 *     codeBlock      → Paragraph with monospace runs + light shading
 *     horizontalRule → empty Paragraph with a bottom border
 *
 *   Inline nodes
 *     text           → TextRun with mark flags
 *     hardBreak      → TextRun({ break: 1 })
 *
 *   Marks
 *     bold / italic / strike → run flags
 *     code                    → font: 'Courier New' on the run
 *     comment                 → DROPPED. Comments are annotations, not
 *                               content. The underlying text is kept. A
 *                               footnote-style export can come later.
 *
 * Nodes / marks not in this list are gracefully degraded: block nodes are
 * recursed for their text content; unknown marks are ignored. That way a
 * schema addition landing before this file catches up produces degraded but
 * non-crashing output.
 */

import {
  AlignmentType,
  BorderStyle,
  Document,
  HeadingLevel,
  LevelFormat,
  Packer,
  Paragraph,
  TextRun,
  type IParagraphOptions,
} from "docx";

interface PMMark {
  type: string;
  attrs?: Record<string, unknown>;
}

export interface PMNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: PMNode[];
  text?: string;
  marks?: PMMark[];
}

const BULLET_REF = "aiper-bullet";
const ORDERED_REF = "aiper-ordered";

const BULLET_MARKERS = ["•", "◦", "▪", "▫"];
const ORDERED_FORMATS = [
  LevelFormat.DECIMAL,
  LevelFormat.LOWER_LETTER,
  LevelFormat.LOWER_ROMAN,
  LevelFormat.DECIMAL,
];

const MAX_LIST_DEPTH = 4;
const INDENT_PER_LEVEL_TWIP = 720; // 0.5in in Word units.

/**
 * Standard OOXML wordprocessingml MIME. Word / Google Docs / macOS Preview /
 * Finder / the `file` command all key off it.
 */
export const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * Generate a `.docx` Blob from a ProseMirror JSON tree.
 *
 * Pure — no DOM access, no side effects. We route through `Packer.toBuffer`
 * and construct the Blob ourselves rather than `Packer.toBlob`, whose Blob
 * polyfill in Node-shaped environments (including jsdom) doesn't implement
 * `arrayBuffer()`.
 */
export async function buildDocxBlob(json: PMNode, title: string): Promise<Blob> {
  const bytes = await buildDocxBytes(json, title);
  const abuf = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
  return new Blob([abuf], { type: DOCX_MIME });
}

/** Same as `buildDocxBlob` but returns the raw bytes — useful for tests. */
export async function buildDocxBytes(
  json: PMNode,
  title: string,
): Promise<Uint8Array> {
  const doc = buildDocument(json, title);
  const buf = await Packer.toBuffer(doc);
  return new Uint8Array(buf);
}

/**
 * Slug for the download filename: lowercased, non-alphanum → '-', hyphens
 * squeezed, no leading/trailing hyphens, capped at 120 chars. Falls back to
 * 'document' when the title normalises to empty.
 */
export function slugifyForDocxFilename(title: string): string {
  const slug = title
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
  return slug || "document";
}

/**
 * Push a Blob into the browser's downloads. DOM side effect — kept out of
 * `buildDocxBlob` so the generator stays testable and pure.
 *
 * The object URL is revoked on the next tick because Safari cancels the
 * download if the URL disappears before it attaches.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ── Internals ────────────────────────────────────────────────────────────────

function buildDocument(json: PMNode, title: string): Document {
  const children = walkBlock(json, { list: null, quoteDepth: 0 });
  // Word rejects a section with zero children — a truly empty document has
  // to hold at least a single empty paragraph.
  if (children.length === 0) children.push(new Paragraph({}));
  return new Document({
    creator: "Aiper",
    title,
    numbering: { config: numberingConfig() },
    sections: [{ children }],
  });
}

interface WalkCtx {
  list: { reference: string; level: number } | null;
  quoteDepth: number;
}

function walkBlock(node: PMNode, ctx: WalkCtx): Paragraph[] {
  switch (node.type) {
    case "doc":
      return (node.content ?? []).flatMap((child) => walkBlock(child, ctx));

    case "paragraph":
      return [paragraph(node, ctx)];

    case "heading": {
      const level = clampHeadingLevel(node.attrs?.["level"]);
      return [
        paragraph(node, ctx, { heading: HEADING_LEVELS[level] }),
      ];
    }

    case "blockquote":
      return (node.content ?? []).flatMap((child) =>
        walkBlock(child, { ...ctx, quoteDepth: ctx.quoteDepth + 1 }),
      );

    case "codeBlock": {
      const code = joinText(node.content ?? []);
      return [
        new Paragraph({
          spacing: { before: 60, after: 60 },
          shading: { fill: "F5F5F5" },
          children: [new TextRun({ text: code, font: "Courier New", size: 20 })],
          ...quoteOptions(ctx),
        }),
      ];
    }

    case "horizontalRule":
      return [
        new Paragraph({
          border: {
            bottom: {
              color: "999999",
              space: 1,
              style: BorderStyle.SINGLE,
              size: 6,
            },
          },
          ...quoteOptions(ctx),
        }),
      ];

    case "bulletList":
    case "orderedList": {
      const reference = node.type === "bulletList" ? BULLET_REF : ORDERED_REF;
      const level = Math.min(
        MAX_LIST_DEPTH - 1,
        ctx.list ? ctx.list.level + 1 : 0,
      );
      return (node.content ?? []).flatMap((child) =>
        walkBlock(child, { ...ctx, list: { reference, level } }),
      );
    }

    case "listItem":
      return (node.content ?? []).flatMap((child) => walkBlock(child, ctx));

    default: {
      // Unknown block — extract the text so we degrade gracefully rather than
      // dropping user content on the floor.
      const text = joinText(node.content ?? []);
      if (!text) return [];
      return [
        new Paragraph({
          children: [new TextRun({ text })],
          ...quoteOptions(ctx),
        }),
      ];
    }
  }
}

function paragraph(
  node: PMNode,
  ctx: WalkCtx,
  extra: Partial<IParagraphOptions> = {},
): Paragraph {
  return new Paragraph({
    children: buildRuns(node.content ?? []),
    ...listOptions(ctx),
    ...quoteOptions(ctx),
    ...extra,
  });
}

function listOptions(ctx: WalkCtx): Partial<IParagraphOptions> {
  if (!ctx.list) return {};
  return {
    numbering: { reference: ctx.list.reference, level: ctx.list.level },
  };
}

function quoteOptions(ctx: WalkCtx): Partial<IParagraphOptions> {
  if (ctx.quoteDepth === 0) return {};
  return { indent: { left: INDENT_PER_LEVEL_TWIP * ctx.quoteDepth } };
}

function buildRuns(inline: PMNode[]): TextRun[] {
  const runs: TextRun[] = [];
  for (const n of inline) {
    if (n.type === "text") {
      const marks = new Set((n.marks ?? []).map((m) => m.type));
      runs.push(
        new TextRun({
          text: n.text ?? "",
          bold: marks.has("bold") || undefined,
          italics: marks.has("italic") || undefined,
          strike: marks.has("strike") || undefined,
          font: marks.has("code") ? "Courier New" : undefined,
        }),
      );
      continue;
    }
    if (n.type === "hardBreak") {
      runs.push(new TextRun({ break: 1 }));
      continue;
    }
    // Any other inline node (e.g. image, when we add it) is dropped for MVP.
  }
  return runs;
}

function joinText(content: PMNode[]): string {
  let out = "";
  const walk = (n: PMNode): void => {
    if (n.type === "text" && typeof n.text === "string") {
      out += n.text;
    } else if (n.type === "hardBreak") {
      out += "\n";
    } else if (n.content) {
      n.content.forEach(walk);
    }
  };
  content.forEach(walk);
  return out;
}

const HEADING_LEVELS = {
  1: HeadingLevel.HEADING_1,
  2: HeadingLevel.HEADING_2,
  3: HeadingLevel.HEADING_3,
} as const;

function clampHeadingLevel(raw: unknown): 1 | 2 | 3 {
  const n = typeof raw === "number" ? raw : parseInt(String(raw), 10);
  if (n === 2) return 2;
  if (n === 3) return 3;
  // Anything else — including undefined, null, non-number, out of range —
  // falls back to H1. StarterKit ships heading.levels [1..4] in v2;
  // exporting H4 as H1 is a harmless degradation.
  return 1;
}

function numberingConfig() {
  const bulletLevels = BULLET_MARKERS.map((marker, level) => ({
    level,
    format: LevelFormat.BULLET,
    text: marker,
    alignment: AlignmentType.LEFT,
    style: {
      paragraph: {
        indent: {
          left: INDENT_PER_LEVEL_TWIP * (level + 1),
          hanging: 360,
        },
      },
    },
  }));
  const orderedLevels = ORDERED_FORMATS.map((format, level) => ({
    level,
    format,
    text: `%${level + 1}.`,
    alignment: AlignmentType.LEFT,
    style: {
      paragraph: {
        indent: {
          left: INDENT_PER_LEVEL_TWIP * (level + 1),
          hanging: 360,
        },
      },
    },
  }));
  return [
    { reference: BULLET_REF, levels: bulletLevels },
    { reference: ORDERED_REF, levels: orderedLevels },
  ];
}
