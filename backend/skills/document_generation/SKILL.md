---
name: document_generation
description: Produce one compliant space-sector deliverable (ECSS specification, ICD, RFP response, technical note) from a template plus the attached sources, with a page-exact citation on every sourced claim.
---

# Document generation

Use this skill when the user asks for a draft, a specification, a response, a
note, or any deliverable that follows a template.

## Procedure

1. Read `/skills/ecss_house_style/SKILL.md` if you have not already this turn.
2. `list_templates`, then `get_template` for the requested template. If none was
   requested, pick the closest match to the user's intent and say which you chose.
3. For **each section** of the template, `search_pages` using the section title
   and its guidance as the query. Retrieval is per page, so every hit is citable
   exactly as returned: `[filename, p.N]`.
4. Delegate the drafting to the `document_writer` sub-agent via `task`, **one
   call per section or per small group of sections**. Paste the retrieved
   excerpts verbatim into the task description together with the section
   number, title and guidance. The writer must never be asked to recall
   anything it was not given.
5. Stage each returned section with `write_file` under
   `/draft/<section-number>.md`, then `read_file` them back in template order to
   assemble the deliverable. This keeps long drafts out of your context window.
6. Assemble under a single H1 title. Close with a `## Source coverage` table:
   one row per section, listing the file and page numbers actually used, and
   flagging any section left `[TBC]`.

## Output contract

- Markdown only. The deliverable itself is the final message: no preamble, no
  "here is your document".
- Sections appear in template order, numbered as in the template.
- Every factual claim from a source carries `[filename, p.N]`.
- A section the sources do not support is written as `[TBC]` with one line on
  what input is required. Never fill a gap with plausible prose.
