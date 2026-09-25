"""What is allowed onto the disk.

Nothing user-controlled ever becomes a filesystem path: assets land as
``{storage_dir}/{owner_id}/{asset_id}{ext}`` and the original name survives
only as metadata — sanitised even there, because it is echoed into the
citations the agent reads and the Vault renders. Bytes are checked against
the extension's magic before they are kept, and streamed against the size
cap in chunks rather than buffered whole.

The magic check here is deliberately allocation-free: it reads a few leading
bytes and nothing else, so a hostile body cannot make the API process do
expensive work before rejection. Deep structure validation (is this zip
really a .docx?) belongs to the parsers, which run inside the sandbox and
turn a spoofed container into a clear indexing error on the asset.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from anyio import Path as AsyncPath
from fastapi import HTTPException, UploadFile, status

_CHUNK = 1024 * 1024
_SNIFF_BYTES = 8192
# The PDF spec allows junk before the header, within the first kilobyte.
_PDF_MAGIC_WINDOW = 1024

# The canonical served type per extension. The client's claimed Content-Type
# is never stored: it is attacker-controlled and disagrees exactly when it
# matters.
CANONICAL_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def sanitize_filename(raw: str | None) -> str:
    """The display/citation name: a bare basename, printable characters only.

    Path separators (both kinds) are cut down to the final component, and
    control and format characters are dropped — Cf includes the bidi
    overrides that make ``fdp.exe`` render as ``exe.pdf``, and Cc includes
    the newlines that would let a name write its own line into a log or an
    agent prompt. Windows-reserved device names are left alone on purpose:
    the name never touches a filesystem, only metadata.

    May return "" (nothing survived); the caller treats that as no filename.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf"))
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > 255:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 15:
            name = f"{stem[: 255 - len(ext) - 1]}.{ext}"
        else:
            name = name[:255]
    return name


def _matches_magic(extension: str, head: bytes) -> bool:
    if extension == ".pdf":
        return b"%PDF-" in head[:_PDF_MAGIC_WINDOW]
    if extension in (".docx", ".pptx"):
        # Every real OOXML file begins with a zip local-file header.
        return head[:4] == b"PK\x03\x04"
    # .txt / .md: any text, but a NUL byte means binary wearing a text name
    # (this also refuses UTF-16, which the text loader cannot read anyway).
    return b"\x00" not in head


async def save_validated(
    file: UploadFile, destination: Path, *, extension: str, max_bytes: int
) -> int:
    """Stream the upload to ``destination``; return the byte count.

    Raises 415 when the leading bytes do not match the extension, 413 the
    moment the stream passes ``max_bytes``, and 422 for an empty file. On any
    failure the partial file is removed; on success the bytes are moved into
    place atomically, so no crash leaves a half-written asset at its final
    path.
    """
    head = await file.read(_SNIFF_BYTES)
    if not head:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The uploaded file is empty"
        )
    if not _matches_magic(extension, head):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"The file's content does not match '{extension}'",
        )

    part = AsyncPath(destination.with_name(destination.name + ".part"))
    size = 0
    try:
        async with await part.open("wb") as out:
            chunk = head
            while chunk:
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        f"File exceeds the {max_bytes // (1024 * 1024)} MB limit",
                    )
                await out.write(chunk)
                chunk = await file.read(_CHUNK)
        await part.rename(destination)
    except BaseException:
        await part.unlink(missing_ok=True)
        raise
    return size
