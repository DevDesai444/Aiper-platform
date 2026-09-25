"use client";

import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Placeholder } from "@tiptap/extensions";
import { EditorContent, useEditor, type JSONContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Eye, GitCommitHorizontal, History, MoreHorizontal, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { CommitDialog } from "@/components/editor/commit-dialog";
import { HistoryPanel } from "@/components/editor/history-panel";
import { ShareDialog } from "@/components/editor/share-dialog";
import { EditorToolbar } from "@/components/editor/toolbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import type { Collaborator, DocumentDetail } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/* StarterKit 3 bundles Link and Underline; only tables and the placeholder are extra. */
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
  ];
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

  const [document, setDocument] = useState(initial);
  const [title, setTitle] = useState(initial.title);
  const [dirty, setDirty] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [commitOpen, setCommitOpen] = useState(false);
  // Traceability is on by default in the data, not on the screen: commits keep
  // recording automatically, and the history is here the moment it is asked for.
  const [historyOpen, setHistoryOpen] = useState(false);

  const readOnly = document.access === "viewer";

  const editor = useEditor(
    {
      extensions: extensions("Start writing, or send a draft here from the chat…"),
      content: document.content_json as unknown as JSONContent,
      editable: !readOnly,
      immediatelyRender: false,
      editorProps: { attributes: { class: "tiptap prose-aiper max-w-none" } },
      onUpdate: () => setDirty(true),
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

  const headline = useMemo(
    () =>
      document.revisions[0]
        ? `r${document.revisions[0].revision_number} · ${document.revisions[0].commit_message}`
        : "No commits yet",
    [document.revisions],
  );

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-6">
          <input
            value={title}
            disabled={readOnly}
            onChange={(event) => setTitle(event.target.value)}
            onBlur={saveTitle}
            onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()}
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

          {!readOnly ? (
            <Button size="sm" disabled={!dirty} onClick={() => setCommitOpen(true)}>
              <GitCommitHorizontal />
              Commit
            </Button>
          ) : null}

          {document.access === "owner" ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Document options">
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem destructive onSelect={() => void remove()}>
                  <Trash2 />
                  Delete document
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </header>

        {editor && !readOnly ? (
          <div className="flex h-11 shrink-0 items-center border-b border-border bg-surface-sunken/60 px-5">
            <EditorToolbar editor={editor} readOnly={readOnly} />
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
    </div>
  );
}
