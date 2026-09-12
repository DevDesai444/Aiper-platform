---
name: feature_comparison
description: Check N source documents against exactly one target document, item by item, and return a compliance matrix with a closed verdict vocabulary and a page citation for every COMPLIANT verdict.
---

# Feature comparison

Use this skill when the user asks whether an offer, datasheet or design meets a
baseline, or asks for a compliance matrix, gap analysis or feature check.

## Procedure

1. `comparison_scope` to confirm which attachment is the target and which are
   sources. If no target is designated, stop and ask the user to mark one.
2. `load_target_document` and read it end to end. Assign a sequential
   identifier `TGT-001, TGT-002, …` to every checkable item: a requirement, a
   constraint, a stated capability. Keep the target's own wording and its
   source page.
3. For each item, `find_evidence_in_sources`. This searches the sources only,
   never the target, so an item can never be shown as satisfied by itself.
4. Delegate verdicts to the `comparison_analyst` sub-agent via `task` in
   **batches of 10–15 items**, with each item's evidence pasted verbatim.
   Collect the rows it returns.
5. Pass all rows to `build_compliance_matrix`. It rejects any verdict outside
   the closed vocabulary; fix and retry rather than editing the table by hand.
6. Finish with a `## Assessment` paragraph and a count per verdict.

## Verdict vocabulary (closed)

| Verdict | Meaning | Evidence rule |
| --- | --- | --- |
| `COMPLIANT` | Source fully satisfies the item | Requires `[file, p.N]` |
| `PARTIAL` | Addressed but short of the item, or only under conditions | State the shortfall with the numbers |
| `NON-COMPLIANT` | Source contradicts the item | Quote both values |
| `NOT ADDRESSED` | No evidence either way | — |

Never soften a numeric shortfall. 30 krad(Si) required against 20 krad(Si)
offered is `PARTIAL` or `NON-COMPLIANT`, not `COMPLIANT`.

## Output contract

One Markdown table with the columns `ID | Target requirement | Verdict |
Evidence | Notes`, followed by the assessment. The matrix is the final message.
