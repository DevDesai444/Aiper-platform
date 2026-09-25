"use client";

import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Placeholder } from "@tiptap/extensions";
import { EditorContent, useEditor, type Editor, type JSONContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Eye,
  FileDown,
  FileUp,
  GitCommitHorizontal,
  History,
  MessageSquarePlus,
  MoreHorizontal,
  Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { CommentMark } from "@/components/editor/comment-mark";
import { CommitDialog } from "@/components/editor/commit-dialog";
import {
  CommentsPanel,
  type CommentAccess,
  type ComposePrompt as CommentComposePrompt,
} from "@/components/editor/comments-panel";
import {
  buildDocxBlob,
  downloadBlob,
  slugifyForDocxFilename,
  type PMNode,
} from "@/components/editor/docx-export";
import { convertDocxToHtml } from "@/components/editor/docx-import";
import { HistoryPanel } from "@/components/editor/history-panel";
import { ShareDialog } from "@/components/editor/share-dialog";
import { EditorToolbar } from "@/components/editor/toolbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Collaborator, DocumentDetail } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/* StarterKit 3 bundles Link and Underline; only tables, placeholder and the
   comment mark are extra. See `comment-mark.ts` for the mark itself. */
function extensions(placeholder: string) {
  return [
    StarterKit.configure({
      heading: { levels: [1, 2, 3, 4] },
      link: { openOnClick: false, autolink: true },
    }),
    Table.configure({ resizable: false }),
    TableRow,
    TableHeader,
    TableCell,
    Placeholder.configure({ placeholder }),
    CommentMark,
  ];
}

interface CapturedSelection extends CommentComposePrompt {
  from: number;
  to: number;
}

export function DocumentEditor({
  initial,
  backHref = "/projects",
}: {
  initial: DocumentDetail;
  /** Where deleting the document returns to. */
  backHref?: string;
}) {
  const router = useRouter();
  const { refreshDocuments } = useWorkspace();
  const { user } = useAuth();

  const [document, setDocument] = useState(initial);
  const [title, setTitle] = useState(initial.title);
  const [dirty, setDirty] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [commitOpen, setCommitOpen] = useState(false);
  // Traceability is on by default in the data, not on the screen: commits keep
  // recording automatically, and the history is here the moment it is asked for.
  const [historyOpen, setHistoryOpen] = useState(false);
  const [hasSelection, setHasSelection] = useState(false);
  const [compose, setCompose] = useState<CapturedSelection | null>(null);
  const [commentsOpen, setCommentsOpen] = useState(false);
  // Bumped whenever the document head changes (commit, restore) so the panel
  // re-fetches — a commit can add/remove `comment` marks.
  const [commentsRefetch, setCommentsRefetch] = useState(0);
  const [pendingImport, setPendingImport] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const readOnly = document.access === "viewer";
  const canWrite = !readOnly;
  const commentAccess = document.access as CommentAccess;

  const editor = useEditor(
    {
      extensions: extensions("Start writing, or send a draft here from the chat…"),
      content: document.content_json as unknown as JSONContent,
      editable: canWrite,
      immediatelyRender: false,
      editorProps: { attributes: { class: "tiptap prose-aiper max-w-none" } },
      onUpdate: () => setDirty(true),
      onSelectionUpdate: ({ editor: ed }) => {
        const { from, to } = ed.state.selection;
        setHasSelection(from !== to);
      },
    },
    [document.id],
  );

  /* Adopt a new head after a commit or restore without losing the caret. */
  useEffect(() => {
    if (!editor) return;
    const current = JSON.stringify(editor.getJSON());
    const head = JSON.stringify(document.content_json);
    if (current !== head && !dirty) {
      editor.commands.setContent(document.content_json as unknown as JSONContent, {
        emitUpdate: false,
      });
    }
  }, [document.content_json, editor, dirty]);

  const commit = useCallback(
    async (message: string) => {
      if (!editor) return;
      setCommitting(true);
      try {
        const next = await api.commit(document.id, {
          content_json: editor.getJSON(),
          commit_message: message,
        });
        setDocument(next);
        setDirty(false);
        setCommitOpen(false);
        setCommentsRefetch((n) => n + 1);
        await refreshDocuments();
        toast.success(`Committed as r${next.revision_count}`);
      } catch (error) {
        toast.error("Could not commit", {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setCommitting(false);
      }
    },
    [document.id, editor, refreshDocuments],
  );

  /* Cmd/Ctrl-S opens the commit dialog when there's something to commit.
     Prevents the browser's Save As from stealing the shortcut, including
     while the editor is focused. */
  useEffect(() => {
    if (!canWrite) return;
    const onKey = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      if (!meta || event.key.toLowerCase() !== "s") return;
      event.preventDefault();
      if (!dirty || committing) return;
      setCommitOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canWrite, dirty, committing]);

  const saveTitle = useCallback(async () => {
    const next = title.trim();
    if (!next || next === document.title) {
      setTitle(document.title);
      return;
    }
    try {
      await api.renameDocument(document.id, next);
      setDocument((current) => ({ ...current, title: next }));
      await refreshDocuments();
    } catch (error) {
      setTitle(document.title);
      toast.error("Could not rename the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }, [document.id, document.title, refreshDocuments, title]);

  async function remove() {
    try {
      await api.deleteDocument(document.id);
      await refreshDocuments();
      router.push(backHref);
    } catch (error) {
      toast.error("Could not delete the document", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  const onCollaborators = useCallback(
    (collaborators: Collaborator[]) =>
      setDocument((current) => ({ ...current, collaborators })),
    [],
  );

  // ── Comments ─────────────────────────────────────────────────────────────

  // Start a compose: capture the selection now, generate the mark id, hand
  // it to the panel. The mark is NOT applied until the POST succeeds.
  const startComment = useCallback(() => {
    if (!editor || !canWrite) return;
    const { from, to } = editor.state.selection;
    if (from === to) return;
    const quotedText = editor.state.doc.textBetween(from, to, "\n", " ").trim();
    setCompose({
      markId: crypto.randomUUID(),
      quotedText: quotedText.slice(0, 4000),
      from,
      to,
    });
  }, [editor, canWrite]);

  const submitCompose = useCallback(
    async (body: string) => {
      if (!editor || !compose) return;
      // POST first — a failed create must not leave an orphan highlight in
      // the editor that any reader could click.
      await api.createComment(document.id, {
        mark_id: compose.markId,
        quoted_text: compose.quotedText,
        body,
      });
      editor
        .chain()
        .focus()
        .setTextSelection({ from: compose.from, to: compose.to })
        .setMark("comment", { markId: compose.markId })
        .run();
      // Applying the mark changed the editor, which will fire onUpdate and
      // set `dirty` — the user needs to commit to persist the highlight.
      setCompose(null);
    },
    [editor, compose, document.id],
  );

  const cancelCompose = useCallback(() => setCompose(null), []);

  // Strip the mark from the local editor after the panel deletes a thread
  // on the server. Same idea as v1's `removeCommentMarkFromEditor`.
  const stripCommentMark = useCallback(
    (markId: string) => {
      if (!editor) return;
      removeCommentMarkFromEditor(editor, markId);
      // Removing a mark dirties the document; a commit is still needed to
      // remove the highlight from persisted history.
    },
    [editor],
  );

  // ── DOCX export / import ─────────────────────────────────────────────────

  const documentTitle = document.title;
  const exportDocx = useCallback(async () => {
    if (!editor || exporting) return;
    setExporting(true);
    try {
      const blob = await buildDocxBlob(
        editor.getJSON() as unknown as PMNode,
        documentTitle,
      );
      downloadBlob(blob, `${slugifyForDocxFilename(documentTitle)}.docx`);
    } catch (error) {
      toast.error("Could not export as DOCX", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setExporting(false);
    }
  }, [editor, exporting, documentTitle]);

  const chooseImportFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const onImportFileChosen = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] ?? null;
      // Reset the input so choosing the same file twice fires change again.
      event.target.value = "";
      if (!file || !editor) return;
      if (isDocumentEmpty(editor)) {
        void applyImportedFile(file);
      } else {
        setPendingImport(file);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [editor],
  );

  const applyImportedFile = useCallback(
    async (file: File) => {
      if (!editor) return;
      setImporting(true);
      try {
        const { html, warnings } = await convertDocxToHtml(file);
        editor.commands.setContent(html || "<p></p>", { emitUpdate: true });
        setDirty(true);
        toast.success("Imported — commit to save the change", {
          description:
            warnings.length > 0
              ? `${warnings.length} note${warnings.length === 1 ? "" : "s"} from the converter (unsupported formatting was dropped).`
              : undefined,
        });
      } catch (error) {
        toast.error("Could not import that DOCX", {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setImporting(false);
      }
    },
    [editor],
  );

  // ── Header text ──────────────────────────────────────────────────────────

  const headline = useMemo(
    () =>
      document.revisions[0]
        ? `r${document.revisions[0].revision_number} · ${document.revisions[0].commit_message}`
        : "No commits yet",
    [document.revisions],
  );

  return (
    <div className="flex min-h-0 flex-1">
      <div className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-6">
          <input
            value={title}
            disabled={readOnly}
            onChange={(event) => setTitle(event.target.value)}
            onBlur={saveTitle}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") {
                setTitle(document.title);
                event.currentTarget.blur();
              }
            }}
            className="min-w-0 flex-1 truncate rounded-md bg-transparent px-1.5 py-1 text-[0.9375rem] font-semibold tracking-[-0.01em] outline-none transition-colors hover:bg-accent/50 focus:bg-accent/50 disabled:hover:bg-transparent"
          />

          {readOnly ? (
            <Badge variant="outline">
              <Eye />
              Read-only
            </Badge>
          ) : dirty ? (
            <Badge variant="warning">Uncommitted changes</Badge>
          ) : (
            <Badge variant="default">{headline}</Badge>
          )}

          <Button
            variant={historyOpen ? "subtle" : "ghost"}
            size="sm"
            onClick={() => setHistoryOpen((open) => !open)}
            aria-pressed={historyOpen}
          >
            <History />
            History
            {document.revision_count > 0 ? (
              <span className="tabular text-muted-foreground">{document.revision_count}</span>
            ) : null}
          </Button>

          <ShareDialog
            document={document}
            canManage={document.access === "owner"}
            onChange={onCollaborators}
          />

          {canWrite ? (
            <Button
              size="sm"
              disabled={!dirty}
              onClick={() => setCommitOpen(true)}
              title="Commit (⌘S)"
            >
              <GitCommitHorizontal />
              Commit
            </Button>
          ) : null}

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Document options">
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => void exportDocx()} disabled={exporting}>
                <FileDown />
                Export as DOCX
              </DropdownMenuItem>
              {canWrite ? (
                <DropdownMenuItem onSelect={chooseImportFile} disabled={importing}>
                  <FileUp />
                  Import DOCX…
                </DropdownMenuItem>
              ) : null}
              {document.access === "owner" ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem destructive onSelect={() => void remove()}>
                    <Trash2 />
                    Delete document
                  </DropdownMenuItem>
                </>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        {editor && canWrite ? (
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-surface-sunken/60 px-5">
            <EditorToolbar editor={editor} readOnly={readOnly} />
            <div className="ml-auto">
              <Button
                variant="ghost"
                size="sm"
                onClick={startComment}
                disabled={!hasSelection}
                title="Comment on the selected text"
              >
                <MessageSquarePlus />
                Comment
              </Button>
            </div>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto bg-surface-sunken/40 px-6 py-8">
          <div className="mx-auto max-w-[820px] rounded-xl border border-border bg-surface px-12 py-12 shadow-subtle">
            <EditorContent editor={editor} />
          </div>
          <p className="mx-auto mt-4 max-w-[820px] text-2xs text-muted-foreground">
            {readOnly
              ? "You have view access to this document."
              : "Changes are not saved until you commit them."}
          </p>
        </div>

        {/* Comments drawer + FAB — absolutely positioned inside the content
            column so the history panel to the right is unaffected. */}
        <CommentsPanel
          documentId={document.id}
          access={commentAccess}
          currentUserId={user?.id ?? null}
          open={commentsOpen}
          onOpenChange={setCommentsOpen}
          compose={compose ? { markId: compose.markId, quotedText: compose.quotedText } : null}
          onSubmitCompose={submitCompose}
          onCancelCompose={cancelCompose}
          onAfterDelete={stripCommentMark}
          refetchNonce={commentsRefetch}
        />

        {/* Hidden native file input, triggered by the "Import DOCX" menu item. */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="hidden"
          onChange={onImportFileChosen}
        />
      </div>

      {historyOpen ? (
        <HistoryPanel document={document} readOnly={readOnly} onRestored={setDocument} />
      ) : null}

      <CommitDialog
        open={commitOpen}
        onOpenChange={setCommitOpen}
        onCommit={(message) => void commit(message)}
        pending={committing}
      />

      <ImportReplaceDialog
        open={pendingImport !== null}
        pending={importing}
        onCancel={() => setPendingImport(null)}
        onConfirm={async () => {
          const file = pendingImport;
          setPendingImport(null);
          if (file) await applyImportedFile(file);
        }}
      />
    </div>
  );
}

// ── DOCX import: confirm before replacing existing content ─────────────────

function ImportReplaceDialog({
  open,
  pending,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Replace the current document?</DialogTitle>
          <DialogDescription>
            Importing a DOCX overwrites the current draft. Your history is
            preserved — you can restore any earlier revision from the panel on
            the right — but the currently uncommitted draft will be replaced.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={onConfirm} loading={pending}>
            <FileUp />
            Replace and import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

/** True when the editor holds an empty ProseMirror document (`<p></p>` etc). */
function isDocumentEmpty(editor: Editor): boolean {
  const doc = editor.state.doc;
  return doc.textContent.trim().length === 0 && doc.childCount <= 1;
}

/**
 * Strip every occurrence of the `comment` mark with `markId === target` from
 * the editor. Called after a successful DELETE so the local editor stops
 * rendering the highlight the same tick the thread disappears from the panel.
 */
function removeCommentMarkFromEditor(editor: Editor, target: string): void {
  const markType = editor.schema.marks.comment;
  if (!markType) return;
  const tr = editor.state.tr;
  let modified = false;
  editor.state.doc.descendants((node, pos) => {
    if (node.marks.length === 0) return;
    for (const m of node.marks) {
      if (m.type === markType && m.attrs.markId === target) {
        tr.removeMark(pos, pos + node.nodeSize, markType);
        modified = true;
      }
    }
  });
  if (modified) editor.view.dispatch(tr);
}
