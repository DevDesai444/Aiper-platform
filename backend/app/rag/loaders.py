"""Page-aware loading. One page in, one retrieval chunk out — no exceptions.

Citation precision is the product. A reviewer must be able to open *file X,
page N* and find the sentence, so the chunk boundary is the page boundary and
never a character count.

The input is untrusted. Loaders run inside the parse sandbox (app.rag.sandbox)
which bounds time and memory; what is bounded *here* is the work we would do
willingly: a page budget checked before per-page extraction begins, and a cap
on the total text a file may expand into. A refusal that the uploader can act
on is raised as `ParseRejected`, whose message is written for them — anything
else that goes wrong stays an ordinary exception and is not shown verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".docx", ".txt", ".md"}

# .docx has no page model until it is rendered; fall back to a paragraph budget.
_DOCX_PARAGRAPHS_PER_PAGE = 40
_TEXT_PAGE_BUDGET = 3500

# A 40 MB upload should never legitimately expand past this much text; a
# decompression bomb will. Checked as pages accumulate, not after.
_MAX_TOTAL_CHARS = 10_000_000


@dataclass(slots=True)
class Page:
    page: int
    text: str


class ParseRejected(ValueError):
    """A refusal whose message is safe — and useful — to show the uploader."""


class UnsupportedFile(ParseRejected):
    pass


class PageLimitExceeded(ParseRejected):
    pass


def _check_page_budget(count: int, max_pages: int | None, unit: str = "pages") -> None:
    if max_pages is not None and count > max_pages:
        raise PageLimitExceeded(
            f"The document has {count} {unit}; the limit is {max_pages}. "
            "Split the file and upload the parts."
        )


def load_pages(
    path: str | Path, extension: str | None = None, *, max_pages: int | None = None
) -> list[Page]:
    """Return one Page per physical page, empty pages dropped and renumbered."""
    p = Path(path)
    ext = (extension or p.suffix).lower()

    if ext == ".pdf":
        raw = _load_pdf(p, max_pages)
    elif ext == ".pptx":
        raw = _load_pptx(p, max_pages)
    elif ext == ".docx":
        raw = _load_docx(p)
    elif ext in {".txt", ".md"}:
        raw = _load_text(p)
    else:
        raise UnsupportedFile(f"Unsupported file type: {ext}")

    return _renumber(raw, max_pages)


def _renumber(texts: list[str], max_pages: int | None = None) -> list[Page]:
    pages: list[Page] = []
    total = 0
    for text in texts:
        cleaned = _normalise(text)
        if cleaned:
            total += len(cleaned)
            if total > _MAX_TOTAL_CHARS:
                raise ParseRejected(
                    "The document expands to more text than can be indexed."
                )
            pages.append(Page(page=len(pages) + 1, text=cleaned))
    _check_page_budget(len(pages), max_pages)
    return pages


def _normalise(text: str) -> str:
    text = text.replace(" ", " ").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_pdf(path: Path, max_pages: int | None) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        # The page budget is checked before any text extraction: page count is
        # read from the page tree, extraction walks every content stream.
        _check_page_budget(len(reader.pages), max_pages)
    except ParseRejected:
        raise
    except PdfReadError as exc:
        raise ParseRejected("The file is not a readable PDF.") from exc

    out: list[str] = []
    for page in reader.pages:
        # A page that will not extract yields "" rather than aborting the file.
        try:
            out.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            out.append("")
    return out


def _load_pptx(path: Path, max_pages: int | None) -> list[str]:
    from zipfile import BadZipFile

    from pptx import Presentation
    from pptx.exc import PackageNotFoundError

    # KeyError: a real zip that is missing the OOXML parts entirely.
    try:
        prs = Presentation(str(path))
    except (PackageNotFoundError, BadZipFile, KeyError) as exc:
        raise ParseRejected("The file is not a valid .pptx presentation.") from exc
    _check_page_budget(len(prs.slides), max_pages, "slides")

    out: list[str] = []
    for slide in prs.slides:
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        if notes.strip():
            parts.append(f"Speaker notes: {notes.strip()}")
        out.append("\n".join(parts))
    return out


def _load_docx(path: Path) -> list[str]:
    from zipfile import BadZipFile

    from docx import Document as DocxDocument
    from docx.opc.exceptions import PackageNotFoundError

    # KeyError: a real zip that is missing the OOXML parts entirely.
    try:
        doc = DocxDocument(str(path))
    except (PackageNotFoundError, BadZipFile, KeyError) as exc:
        raise ParseRejected("The file is not a valid .docx document.") from exc

    pages: list[str] = []
    current: list[str] = []

    def flush() -> None:
        pages.append("\n".join(current))
        current.clear()

    for para in doc.paragraphs:
        if "w:br" in para._p.xml and 'type="page"' in para._p.xml:
            flush()
        if para.text.strip():
            current.append(para.text.strip())
        if len(current) >= _DOCX_PARAGRAPHS_PER_PAGE:
            flush()

    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                current.append(" | ".join(cells))

    if current:
        flush()
    return pages


def _load_text(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if "\f" in raw:
        return raw.split("\f")

    # No explicit page breaks: fill a budget, but only break on paragraphs.
    pages: list[str] = []
    buffer = ""
    for block in raw.split("\n\n"):
        if buffer and len(buffer) + len(block) > _TEXT_PAGE_BUDGET:
            pages.append(buffer)
            buffer = block
        else:
            buffer = f"{buffer}\n\n{block}" if buffer else block
    if buffer:
        pages.append(buffer)
    return pages
