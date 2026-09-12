"use client";

import { FileText } from "lucide-react";
import { Fragment, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A small, purpose-built Markdown renderer.
 *
 * It exists rather than a library because two things need first-class treatment:
 * page citations like `[MIS-REQ.pdf, p.2]`, and the closed verdict vocabulary.
 */

const VERDICTS = ["COMPLIANT", "PARTIAL", "NON-COMPLIANT", "NOT ADDRESSED"] as const;

const VERDICT_STYLES: Record<string, string> = {
  COMPLIANT: "border-verdict-pass/25 bg-verdict-pass-bg text-verdict-pass",
  PARTIAL: "border-verdict-partial/25 bg-verdict-partial-bg text-verdict-partial",
  "NON-COMPLIANT": "border-verdict-fail/25 bg-verdict-fail-bg text-verdict-fail",
  "NOT ADDRESSED": "border-verdict-none/25 bg-verdict-none-bg text-verdict-none",
};

export function VerdictChip({ verdict }: { verdict: string }) {
  const key = verdict.toUpperCase().trim();
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-md border px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide",
        VERDICT_STYLES[key] ?? VERDICT_STYLES["NOT ADDRESSED"],
      )}
    >
      {key}
    </span>
  );
}

function Citation({ label }: { label: string }) {
  return (
    <span className="mx-0.5 inline-flex items-baseline gap-1 rounded-md border border-border bg-surface-sunken px-1.5 py-px align-baseline font-mono text-[0.6875em] text-muted-foreground">
      <FileText className="mt-[0.2em] h-2.5 w-2.5 shrink-0" aria-hidden />
      {label}
    </span>
  );
}

/* ── inline ───────────────────────────────────────────────────────────── */

// Citation, bold, italic, inline code, link.
const INLINE =
  /(\[[^\]\n]+?,\s*(?:p\.|pp\.)\s*\d+[^\]\n]*\])|(\*\*[^*]+\*\*)|(`[^`]+`)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\([^)\s]+\))/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  const verdictPattern = new RegExp(`\\b(${VERDICTS.join("|")})\\b`, "g");
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let index = 0;

  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(...verdicts(text.slice(cursor, start), `${keyPrefix}-t${index}`));

    const token = match[0];
    const key = `${keyPrefix}-i${index++}`;
    if (match[1]) nodes.push(<Citation key={key} label={token.slice(1, -1)} />);
    else if (match[2]) nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else if (match[3]) nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (match[4]) nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    else if (match[5]) {
      const [, label, href] = token.match(/\[([^\]]+)\]\(([^)\s]+)\)/) ?? [];
      nodes.push(
        <a key={key} href={href} target="_blank" rel="noreferrer noopener">
          {label}
        </a>,
      );
    }
    cursor = start + token.length;
  }

  if (cursor < text.length) nodes.push(...verdicts(text.slice(cursor), `${keyPrefix}-tail`));
  return nodes;

  function verdicts(chunk: string, prefix: string): ReactNode[] {
    const out: ReactNode[] = [];
    let last = 0;
    let n = 0;
    for (const match of chunk.matchAll(verdictPattern)) {
      const start = match.index ?? 0;
      if (start > last) out.push(<Fragment key={`${prefix}-p${n}`}>{chunk.slice(last, start)}</Fragment>);
      out.push(<VerdictChip key={`${prefix}-v${n++}`} verdict={match[0]} />);
      last = start + match[0].length;
    }
    if (last < chunk.length) out.push(<Fragment key={`${prefix}-end`}>{chunk.slice(last)}</Fragment>);
    return out;
  }
}

/* ── block ────────────────────────────────────────────────────────────── */

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|(.+)\|\s*$/;
const TABLE_SEP = /^\s*\|[\s:|-]+\|\s*$/;
const QUOTE = /^>\s?(.*)$/;
const RULE = /^(-{3,}|\*{3,}|_{3,})\s*$/;

function cells(line: string) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function Markdown({ content, className }: { content: string; className?: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${Math.min(level, 6)}` as "h1";
      blocks.push(<Tag key={key++}>{inline(heading[2], `h${key}`)}</Tag>);
      i += 1;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push(<hr key={key++} />);
      i += 1;
      continue;
    }

    if (TABLE_ROW.test(line)) {
      const rows: string[][] = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        if (!TABLE_SEP.test(lines[i])) rows.push(cells(lines[i]));
        i += 1;
      }
      const [header, ...body] = rows;
      blocks.push(
        <div key={key++} className="-mx-1 overflow-x-auto px-1">
          <table>
            {header ? (
              <thead>
                <tr>
                  {header.map((cell, index) => (
                    <th key={index}>{inline(cell, `th${key}-${index}`)}</th>
                  ))}
                </tr>
              </thead>
            ) : null}
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}>{inline(cell, `td${key}-${rowIndex}-${cellIndex}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        quoted.push(QUOTE.exec(lines[i])![1]);
        i += 1;
      }
      blocks.push(<blockquote key={key++}>{inline(quoted.join(" "), `q${key}`)}</blockquote>);
      continue;
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      const ordered = ORDERED.test(line);
      const items: string[] = [];
      while (i < lines.length) {
        const match = ordered ? ORDERED.exec(lines[i]) : BULLET.exec(lines[i]);
        if (!match) break;
        items.push(ordered ? match[2] : match[1]);
        i += 1;
      }
      const Tag = ordered ? "ol" : "ul";
      blocks.push(
        <Tag key={key++}>
          {items.map((item, index) => (
            <li key={index}>{inline(item, `li${key}-${index}`)}</li>
          ))}
        </Tag>,
      );
      continue;
    }

    const paragraph: string[] = [line.trim()];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !HEADING.test(lines[i]) &&
      !BULLET.test(lines[i]) &&
      !ORDERED.test(lines[i]) &&
      !TABLE_ROW.test(lines[i]) &&
      !QUOTE.test(lines[i]) &&
      !RULE.test(lines[i])
    ) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    blocks.push(<p key={key++}>{inline(paragraph.join(" "), `p${key}`)}</p>);
  }

  return <div className={cn("prose-aiper", className)}>{blocks}</div>;
}
