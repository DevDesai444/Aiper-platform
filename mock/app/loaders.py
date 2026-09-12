"""Page-aware loading. One page in, one retrieval chunk out — no exceptions.

Citation precision is the product. A reviewer must be able to open *file X,
page N* and find the sentence, so the chunk boundary is the page boundary and
never a character count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".docx", ".txt", ".md"}

# .docx has no page model until it is rendered; fall back to a paragraph budget.
_DOCX_PARAGRAPHS_PER_PAGE = 40
_TEXT_PAGE_BUDGET = 3500


@dataclass(slots=True)
class Page:
    page: int
    text: str


class UnsupportedFile(ValueError):
    pass


def load_pages(path: str | Path, extension: str | None = None) -> list[Page]:
    """Return one Page per physical page, empty pages dropped and renumbered."""
    p = Path(path)
    ext = (extension or p.suffix).lower()

    if ext == ".pdf":
        raw = _load_pdf(p)
    elif ext == ".pptx":
        raw = _load_pptx(p)
    elif ext == ".docx":
        raw = _load_docx(p)
    elif ext in {".txt", ".md"}:
        raw = _load_text(p)
    else:
        raise UnsupportedFile(f"Unsupported file type: {ext}")

    return _renumber(raw)


def _renumber(texts: list[str]) -> list[Page]:
    pages: list[Page] = []
    for text in texts:
        cleaned = _normalise(text)
        if cleaned:
            pages.append(Page(page=len(pages) + 1, text=cleaned))
    return pages


def _normalise(text: str) -> str:
    text = text.replace(" ", " ").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out: list[str] = []
    for page in reader.pages:
        # A page that will not extract yields "" rather than aborting the file.
        try:
            out.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            out.append("")
    return out


def _load_pptx(path: Path) -> list[str]:
    from pptx import Presentation

    prs = Presentation(str(path))
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
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
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
