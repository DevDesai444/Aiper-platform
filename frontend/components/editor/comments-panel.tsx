"use client";

/**
 * Comments drawer + floating trigger, ported from v1's `CommentsPanel.tsx`
 * onto v2's Tailwind/shadcn idiom.
 *
 * Data model recap (see `comment-mark.ts` for the mark):
 *   • Highlight  → TipTap `comment` mark carrying `markId`, lives in
 *                  `documents.content_json`.
 *   • Body       → REST row keyed `(document_id, mark_id)`, lives in
 *                  `document_comments`.
 *
 * Refresh model (single-editor, no Yjs)
 *   • Open drawer.
 *   • After a local mutation (add / resolve / delete).
 *   • Bumped by the parent via `refetchNonce` — the caller does this after a
 *     commit or restore so a thread whose mark disappeared falls off, and a
 *     mark that came back from history reappears.
 *
 * Role rules (mirror the server's `require_access` gate):
 *   viewer  — reads threads; no compose / resolve / delete UI.
 *   editor  — adds, resolves, and deletes threads they authored.
 *   owner   — everything editor can do, plus delete anyone's thread.
 */

import { formatDistanceToNow } from "date-fns";
import { Check, MessageCircle, MessageSquarePlus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { DocumentComment } from "@/lib/types";
import { cn } from "@/lib/utils";

export type CommentAccess = "owner" | "editor" | "viewer" | "shared" | null;

export interface ComposePrompt {
  markId: string;
  quotedText: string;
}

export interface CommentsPanelProps {
  documentId: string;
  access: CommentAccess;
  currentUserId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Non-null while the parent is holding a captured selection waiting for a body. */
  compose: ComposePrompt | null;
  /** Post the body — parent applies the mark after this resolves. */
  onSubmitCompose: (body: string) => Promise<void>;
  onCancelCompose: () => void;
  /** Called after a delete succeeds so the parent can strip the mark from the editor. */
  onAfterDelete: (markId: string) => void;
  /** Bumped by the parent to force a refetch (e.g. after a commit or restore). */
  refetchNonce?: number;
}

interface Thread {
  markId: string;
  comments: DocumentComment[];
  quotedText: string;
  resolved: boolean;
}

function canRead(access: CommentAccess): boolean {
  return access !== null;
}

function canWrite(access: CommentAccess): boolean {
  return access === "editor" || access === "owner" || access === "shared";
}

export function CommentsPanel({
  documentId,
  access,
  currentUserId,
  open,
  onOpenChange,
  compose,
  onSubmitCompose,
  onCancelCompose,
  onAfterDelete,
  refetchNonce = 0,
}: CommentsPanelProps) {
  const [comments, setComments] = useState<DocumentComment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [localRefetch, setLocalRefetch] = useState(0);
  const editable = canWrite(access);
  const isOwner = access === "owner";

  // Force-open when a compose prompt arrives — we never expect a compose to
  // start while the drawer is closed.
  useEffect(() => {
    if (compose) onOpenChange(true);
  }, [compose, onOpenChange]);

  useEffect(() => {
    if (!open) return;
    const ac = new AbortController();
    setError(null);
    api
      .listComments(documentId)
      .then((list) => {
        if (ac.signal.aborted) return;
        setComments(list);
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setError(describeError(err));
      });
    return () => ac.abort();
  }, [open, documentId, localRefetch, refetchNonce]);

  const threads = useMemo(
    () => (comments ? groupThreads(comments) : []),
    [comments],
  );

  const handleSubmitCompose = useCallback(
    async (body: string) => {
      await onSubmitCompose(body);
      setLocalRefetch((n) => n + 1);
    },
    [onSubmitCompose],
  );

  const handleResolve = useCallback(
    async (markId: string) => {
      await api.resolveCommentThread(documentId, markId);
      setLocalRefetch((n) => n + 1);
    },
    [documentId],
  );

  const handleDelete = useCallback(
    async (markId: string) => {
      await api.deleteCommentThread(documentId, markId);
      onAfterDelete(markId);
      setLocalRefetch((n) => n + 1);
    },
    [documentId, onAfterDelete],
  );

  const handleFocus = useCallback((markId: string) => {
    const target = document.querySelector<HTMLElement>(
      `.tiptap [data-comment-id="${cssEscape(markId)}"]`,
    );
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("comment-highlight--flash");
    window.setTimeout(
      () => target.classList.remove("comment-highlight--flash"),
      1200,
    );
  }, []);

  if (!canRead(access)) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onOpenChange(true)}
        aria-label="Open comments"
        className="pointer-events-auto absolute bottom-4 right-4 z-10 flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-[0.8125rem] font-medium text-foreground shadow-elevated transition-colors hover:bg-accent"
      >
        <MessageCircle className="size-3.5" />
        Comments
      </button>
    );
  }

  return (
    <aside
      role="complementary"
      aria-label="Document comments"
      className="pointer-events-auto absolute inset-y-0 right-0 z-10 flex w-[320px] flex-col border-l border-border bg-surface shadow-elevated"
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
        <MessageCircle className="size-3.5 text-muted-foreground" />
        <span className="text-[0.8125rem] font-medium">Comments</span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label="Close comments"
          onClick={() => onOpenChange(false)}
        >
          <X />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {compose && editable && (
          <ComposeForm
            key={compose.markId}
            prompt={compose}
            onSubmit={handleSubmitCompose}
            onCancel={onCancelCompose}
          />
        )}

        {error ? (
          <div className="rounded-md border border-border bg-surface-sunken/50 p-3 text-2xs">
            <p className="text-muted-foreground">{error}</p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mt-2"
              onClick={() => setLocalRefetch((n) => n + 1)}
            >
              Retry
            </Button>
          </div>
        ) : comments === null ? (
          <p className="px-1 py-2 text-2xs text-muted-foreground">Loading…</p>
        ) : threads.length === 0 && !compose ? (
          <p className="px-1 py-2 text-2xs text-muted-foreground">
            No comments yet — select text and click{" "}
            <MessageSquarePlus className="inline size-3 align-text-bottom" /> to
            start a thread.
          </p>
        ) : (
          <ol className="space-y-2">
            {threads.map((t) => (
              <ThreadCard
                key={t.markId}
                thread={t}
                canWrite={editable}
                isOwner={isOwner}
                currentUserId={currentUserId}
                onResolve={handleResolve}
                onDelete={handleDelete}
                onFocus={handleFocus}
              />
            ))}
          </ol>
        )}
      </div>

      <p className="shrink-0 border-t border-border px-4 py-3 text-2xs leading-relaxed text-muted-foreground">
        A highlight is saved with the next commit — the comment itself is saved
        as soon as you post it.
      </p>
    </aside>
  );
}

// ── ComposeForm ─────────────────────────────────────────────────────────────

function ComposeForm({
  prompt,
  onSubmit,
  onCancel,
}: {
  prompt: ComposePrompt;
  onSubmit: (body: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = body.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(trimmed);
      setBody("");
      toast.success("Comment posted");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-3 space-y-2 rounded-md border border-border bg-surface-sunken/40 p-3"
    >
      {prompt.quotedText ? (
        <blockquote className="border-l-2 border-border-strong pl-2 text-2xs text-muted-foreground">
          &ldquo;{truncate(prompt.quotedText, 120)}&rdquo;
        </blockquote>
      ) : null}
      <Textarea
        autoFocus
        rows={3}
        placeholder="Add a comment…"
        value={body}
        disabled={submitting}
        onChange={(event) => setBody(event.target.value)}
        aria-label="Comment body"
      />
      {error ? <p className="text-2xs text-destructive">{error}</p> : null}
      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={submitting} disabled={!body.trim()}>
          Comment
        </Button>
      </div>
    </form>
  );
}

// ── ThreadCard ──────────────────────────────────────────────────────────────

function ThreadCard({
  thread,
  canWrite: editable,
  isOwner,
  currentUserId,
  onResolve,
  onDelete,
  onFocus,
}: {
  thread: Thread;
  canWrite: boolean;
  isOwner: boolean;
  currentUserId: string | null;
  onResolve: (markId: string) => Promise<void>;
  onDelete: (markId: string) => Promise<void>;
  onFocus: (markId: string) => void;
}) {
  const [busy, setBusy] = useState<"resolve" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A non-owner editor can only delete a thread whose every comment they
  // authored — the server enforces the same rule and would 403 otherwise.
  const canDelete =
    isOwner ||
    (editable &&
      currentUserId != null &&
      thread.comments.every((c) => c.author_id === currentUserId));

  async function withBusy<T>(kind: "resolve" | "delete", fn: () => Promise<T>) {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li
      className={cn(
        "rounded-md border border-border bg-surface p-3 text-[0.8125rem]",
        thread.resolved && "opacity-60",
      )}
    >
      <button
        type="button"
        onClick={() => onFocus(thread.markId)}
        className="mb-2 block w-full truncate rounded text-left text-2xs text-muted-foreground transition-colors hover:text-foreground"
        title="Scroll to highlight"
      >
        {thread.quotedText
          ? `“${truncate(thread.quotedText, 80)}”`
          : "Selection"}
      </button>

      <ol className="space-y-2">
        {thread.comments.map((c) => (
          <li key={c.id}>
            <div className="flex items-baseline justify-between gap-2 text-2xs text-muted-foreground">
              <span className="truncate font-medium text-foreground">
                {c.author_name || c.author_email}
              </span>
              <time
                dateTime={c.created_at}
                title={new Date(c.created_at).toLocaleString()}
              >
                {formatWhen(c.created_at)}
              </time>
            </div>
            <p className="mt-0.5 whitespace-pre-wrap text-[0.8125rem] leading-snug">
              {c.body}
            </p>
          </li>
        ))}
      </ol>

      {(editable || canDelete) && (
        <div className="mt-2 flex items-center gap-2">
          {editable && !thread.resolved && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => void withBusy("resolve", () => onResolve(thread.markId))}
              disabled={busy !== null}
              title="Resolve thread"
            >
              <Check />
              {busy === "resolve" ? "Resolving…" : "Resolve"}
            </Button>
          )}
          {canDelete && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => void withBusy("delete", () => onDelete(thread.markId))}
              disabled={busy !== null}
              title="Delete thread"
            >
              <Trash2 />
              {busy === "delete" ? "Deleting…" : "Delete"}
            </Button>
          )}
        </div>
      )}
      {error ? (
        <p className="mt-1 text-2xs text-destructive">{error}</p>
      ) : null}
    </li>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Group a flat, oldest-first list into threads by `markId`. Preserves order:
 * threads sort by their first comment's `createdAt`, comments within a
 * thread stay in server order.
 *
 * A thread is `resolved` when every comment on it has a `resolvedAt` — the
 * per-mark resolve on the server sets them all at once, so this always ends
 * up all-or-nothing.
 */
function groupThreads(comments: DocumentComment[]): Thread[] {
  const byMark = new Map<string, Thread>();
  for (const c of comments) {
    let t = byMark.get(c.mark_id);
    if (!t) {
      t = {
        markId: c.mark_id,
        comments: [],
        quotedText: c.quoted_text,
        resolved: false,
      };
      byMark.set(c.mark_id, t);
    }
    t.comments.push(c);
    t.resolved = t.comments.every((x) => x.resolved_at !== null);
  }
  return Array.from(byMark.values());
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return formatDistanceToNow(d, { addSuffix: true });
}

/**
 * CSS.escape polyfill for older environments (jsdom / older Safari). Our
 * `markId`s are uuids and safe already, but being defensive keeps this from
 * breaking if a future id ever carries a quote or backslash.
 */
function cssEscape(s: string): string {
  const w = window as unknown as { CSS?: { escape?: (s: string) => string } };
  if (w.CSS?.escape) return w.CSS.escape(s);
  return s.replace(/["\\]/g, "\\$&");
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
