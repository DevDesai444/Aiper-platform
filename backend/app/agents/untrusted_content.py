"""Defends the boundary where retrieved, user-uploaded document text enters
the agent's message stream.

Any user can upload a PDF whose text tries to impersonate a system prompt, a
role header, or a tool result, hoping the model treats it as instructions
instead of quoted material. The product's citations depend on the quoted text
staying byte-exact (see `SUPERVISOR_BASE` in `prompts.py`: values are never
rounded, adjusted or "tidied"), so defense here is structural framing plus an
explicit prompt rule — this module never rewrites or strips what a page
actually says.

Exactly one mutation ever happens, and it targets our own delimiter, not the
attacker's content: a literal `<doc>`/`</doc>` occurring inside retrieved
text is defused so it cannot forge a second boundary and smuggle fake content
after it. Everything else — fake "System:" headers, "ignore previous
instructions", JSON that looks like a tool call — is left exactly as
retrieved; `UNTRUSTED_CONTENT_RULE` in `prompts.py` is what tells the model
none of that carries any authority.
"""

from __future__ import annotations

_ZWSP = "​"


def _defuse_tag_lookalikes(text: str) -> str:
    """Break any literal `<doc>` / `</doc>` inside untrusted text.

    A zero-width space splits the tag name so it no longer matches the
    literal marker string, while rendering visually identical to a reader —
    the defense is against a parser being fooled, not against a human seeing
    the text, and it never touches anything else in the string.
    """
    return text.replace("<doc>", f"<{_ZWSP}doc>").replace("</doc>", f"<{_ZWSP}/doc>")


def wrap_untrusted(text: str) -> str:
    """Fence one block of retrieved document text as data, not instructions.

    Callers keep any citation line (`[filename, p.N]`) *outside* this
    wrapper — only the quoted text itself goes inside `<doc>...</doc>`, so
    the citation format every prompt and subagent already parses is
    untouched.
    """
    return f"<doc>\n{_defuse_tag_lookalikes(text)}\n</doc>"
