import { Mark, mergeAttributes } from "@tiptap/core";

/**
 * `comment` — inline mark that anchors a REST comment thread to a range of
 * text.
 *
 * Data model
 *   The mark carries a single `markId` attribute (a client-generated uuid).
 *   Bodies + authorship + resolved state live in the `document_comments`
 *   table on the server, keyed by `(document_id, mark_id)`. The mark itself
 *   lives in `documents.content_json` — the ProseMirror JSON we persist at
 *   commit time — so:
 *
 *     • The highlight survives a page reload because it round-trips through
 *       the committed JSON.
 *     • Every reader of the current head sees the same highlight on the same
 *       range.
 *     • Marks added since the last commit are LOCAL until the user commits;
 *       an uncommitted highlight is lost if the tab closes, which is why we
 *       nudge the user to commit after adding a comment. The comment row on
 *       the server is created immediately either way.
 *
 * Serialisation
 *   Renders as `<span data-comment-id="…" class="comment-highlight">`, which
 *   is what the panel selects on to scroll a thread into view. The mark is
 *   `inclusive: false`: typing at the edge of a commented run does NOT extend
 *   the highlight — otherwise a character appended at the end would carry
 *   the mark into unrelated text and drag the thread's textual anchor with
 *   it.
 *
 * Schema is forever
 *   The mark name `comment` and the attribute name `markId` are permanent
 *   on-wire keys. Renaming either would silently orphan every existing
 *   highlight across every stored document. Additive changes (new
 *   attributes, new marks) are safe; renames are not.
 */
export const CommentMark = Mark.create({
  name: "comment",

  // A commented range should not extend when the user types at its end — see
  // the docstring above.
  inclusive: false,

  // Free overlap with any other mark: a bolded phrase can be commented, a
  // commented phrase can be italicised, and two independent threads can
  // overlap on the same run (each with its own `markId`). `excludes: ''`
  // means "this mark excludes no other marks".
  excludes: "",

  addAttributes() {
    return {
      markId: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-comment-id"),
        renderHTML: (attrs) =>
          attrs.markId ? { "data-comment-id": attrs.markId } : {},
      },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-comment-id]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, { class: "comment-highlight" }),
      0,
    ];
  },
});
