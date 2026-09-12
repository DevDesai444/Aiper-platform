---
name: ecss_house_style
description: Writing conventions for European space-sector deliverables (ECSS): requirement wording, identifiers, verification methods, units and citation format. Read before drafting or reviewing any document section.
---

# ECSS house style

## Requirements

- One requirement per numbered item, written as a single testable statement
  using **shall**. Descriptive text uses *is* / *will*; recommendations use
  *should*.
- Every requirement has a unique identifier in the form `<PREFIX>-<NNN>`
  (for example `MIS-REQ-014`). Carry identifiers from the sources through
  unchanged.
- Each requirement names its verification method: **Test**, **Analysis**,
  **Review of Design**, or **Inspection**.

## Numbers and units

- Preserve every value exactly as the source states it, with its unit and
  tolerance: `30 krad(Si)`, `−20 °C to +50 °C`, `400 Mbit/s`, `14.1 g RMS`.
- Never round, convert or "tidy" a value. If two sources disagree, quote both.

## Citations

- Format: `[filename, p.N]`, placed at the end of the sentence it supports.
- Page numbers come only from the retrieval tools. Never guess, adjust or infer
  a page.

## Document structure

- Clause numbering follows the template (`4`, `4.1`, `4.1.1`).
- Applicable documents are listed with their full ECSS designation, for
  example `ECSS-E-ST-10C Rev.1`.
- Gaps are written as `[TBC]` (to be confirmed) or `[TBD]` (to be defined),
  each followed by one line naming the missing input.
