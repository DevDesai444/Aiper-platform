"""Tools, bound to one request.

Every tool is a closure over a `SkillContext`. That is how isolation is
enforced at the tool layer rather than by prompt instruction: the agent cannot
pass an owner or an organisation, because it never sees either. Every
retrieval re-derives the caller's `RetrievalScope` from the database at call
time (`app.rag.scope`), so what a tool can reach is exactly what the access
resolver admits at that moment — a grant revoked mid-turn is gone by the next
tool call. The scope is applied inside the store as a mandatory Qdrant filter;
nothing the model emits can widen it.

Retrieved page text is the other half of the trust boundary: it is
user-uploaded content, not instructions, no matter what it contains. Every
tool below that returns page text runs it through
`app.agents.untrusted_content.wrap_untrusted` before it ever becomes a tool
result — see that module and `UNTRUSTED_CONTENT_RULE` in `prompts.py` for the
full defense. Citation lines (`[filename, p.N]`) stay outside the wrapper,
unmodified, exactly as retrieved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.agents.untrusted_content import wrap_untrusted
from app.rag import store
from app.rag.scope import RetrievalScope, ScopeProvider, scope_provider


@dataclass(slots=True)
class SkillContext:
    owner_id: uuid.UUID
    # The tenancy the request runs under. org_id=None fails closed: a scope
    # without an organisation retrieves nothing at all.
    org_id: uuid.UUID | None = None
    scope_provider: ScopeProvider | None = None
    mode: str = "document_generation"
    attachment_ids: list[uuid.UUID] = field(default_factory=list)
    target_attachment_id: uuid.UUID | None = None
    template_key: str | None = None
    templates: list[dict[str, Any]] = field(default_factory=list)
    filenames: dict[str, str] = field(default_factory=dict)

    @property
    def source_ids(self) -> list[uuid.UUID]:
        return [i for i in self.attachment_ids if i != self.target_attachment_id]

    async def fresh_scope(self) -> RetrievalScope:
        provider = self.scope_provider or scope_provider(
            user_id=self.owner_id, org_id=self.org_id
        )
        return await provider()


def _render(hits: list[store.Hit], *, empty: str) -> str:
    if not hits:
        return empty
    return "\n\n".join(f"{h.citation()}\n{wrap_untrusted(h.text.strip())}" for h in hits)


def build_tools(ctx: SkillContext) -> dict[str, BaseTool]:
    """Resolve the toolset for this turn, keyed by name."""

    @tool
    async def search_pages(query: str, limit: int = 6) -> str:
        """Search the indexed pages of the attached documents for a topic.

        Returns whole pages with their exact citation. Use the section title, the
        requirement wording or a key term as the query.
        """
        hits = await store.search(
            scope=await ctx.fresh_scope(),
            query=query,
            limit=max(1, min(limit, 12)),
            file_ids=ctx.attachment_ids or None,
        )
        return _render(hits, empty=f"No indexed page matched '{query}'.")

    @tool
    async def find_evidence_in_sources(requirement: str, limit: int = 5) -> str:
        """Search the SOURCE documents only for evidence about one target item.

        The target document is excluded, so a requirement can never be shown as
        satisfied by itself.
        """
        source_ids = ctx.source_ids
        if not source_ids:
            return "No source documents were attached — only a target."
        hits = await store.search(
            scope=await ctx.fresh_scope(),
            query=requirement,
            limit=max(1, min(limit, 10)),
            file_ids=source_ids,
        )
        return _render(hits, empty="No evidence found in the sources. Verdict: NOT ADDRESSED.")

    @tool
    async def load_target_document() -> str:
        """Read the comparison target end to end, page by page, in order."""
        if not ctx.target_attachment_id:
            return "No target document was designated for this comparison."
        pages = await store.read_file_pages(
            scope=await ctx.fresh_scope(), file_id=ctx.target_attachment_id
        )
        if not pages:
            return "The target document has no indexed pages."
        name = pages[0].filename
        body = "\n\n".join(
            f"--- {name}, p.{p.page} ---\n{wrap_untrusted(p.text)}" for p in pages
        )
        return f"Target document: {name} ({len(pages)} pages)\n\n{body}"

    @tool
    def list_attachments() -> str:
        """List the documents attached to this turn and their role."""
        if not ctx.attachment_ids:
            return "No documents are attached to this turn."
        lines = []
        for fid in ctx.attachment_ids:
            role = "TARGET" if fid == ctx.target_attachment_id else "source"
            lines.append(f"- {ctx.filenames.get(str(fid), str(fid))} ({role})")
        return "\n".join(lines)

    @tool
    def comparison_scope() -> str:
        """Confirm the direction of the comparison before any verdict is assigned."""
        if not ctx.target_attachment_id:
            return "No target designated. Ask the user which document is the target."
        target = ctx.filenames.get(str(ctx.target_attachment_id), "unknown")
        sources = [ctx.filenames.get(str(i), str(i)) for i in ctx.source_ids]
        return (
            f"Target (the document being checked against): {target}\n"
            f"Sources ({len(sources)}): {', '.join(sources) or 'none'}\n"
            "Items are extracted from the target; evidence is drawn from the sources."
        )

    @tool
    def list_templates() -> str:
        """List the document templates available in this workspace."""
        if not ctx.templates:
            return "No templates available."
        lines = []
        for t in ctx.templates:
            marker = " (requested)" if t.get("key") == ctx.template_key else ""
            standard = f" — {t['standard']}" if t.get("standard") else ""
            lines.append(f"- {t['key']}: {t['name']}{standard}{marker}")
        return "\n".join(lines)

    @tool
    def get_template(key: str) -> str:
        """Fetch one template's full section structure and per-section guidance."""
        match = next((t for t in ctx.templates if t.get("key") == key), None)
        if match is None:
            available = ", ".join(t.get("key", "") for t in ctx.templates)
            return f"No template '{key}'. Available: {available}"
        lines = [f"# {match['name']}"]
        if match.get("standard"):
            lines.append(f"Standard: {match['standard']}")
        if match.get("description"):
            lines.append(match["description"])
        lines.append("\nSections:")
        for section in match.get("sections", []):
            number = section.get("number", "")
            lines.append(f"{number} {section.get('title', '')}".strip())
            if section.get("guidance"):
                lines.append(f"    guidance: {section['guidance']}")
        return "\n".join(lines)

    @tool
    def build_compliance_matrix(rows: list[dict[str, str]]) -> str:
        """Render finished verdict rows as one Markdown compliance matrix.

        Each row takes the keys: id, requirement, verdict, evidence, notes.
        Verdicts outside the closed vocabulary are rejected.
        """
        allowed = {"COMPLIANT", "PARTIAL", "NON-COMPLIANT", "NOT ADDRESSED"}
        header = (
            "| ID | Target requirement | Verdict | Evidence | Notes |\n"
            "| --- | --- | --- | --- | --- |"
        )
        body: list[str] = []
        tally: dict[str, int] = {}
        for row in rows:
            verdict = str(row.get("verdict", "")).upper().strip()
            if verdict not in allowed:
                return f"Rejected: '{verdict}' is not a permitted verdict. Use one of {allowed}."
            tally[verdict] = tally.get(verdict, 0) + 1
            cells = [
                row.get("id", ""),
                row.get("requirement", ""),
                verdict,
                row.get("evidence", ""),
                row.get("notes", ""),
            ]
            body.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        summary = " · ".join(f"{k}: {v}" for k, v in sorted(tally.items()))
        return f"{header}\n" + "\n".join(body) + f"\n\nTotals — {summary}"

    tools = [
        search_pages,
        find_evidence_in_sources,
        load_target_document,
        list_attachments,
        comparison_scope,
        list_templates,
        get_template,
        build_compliance_matrix,
    ]
    return {t.name: t for t in tools}
