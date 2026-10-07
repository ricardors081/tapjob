"""Bounded plain-text extraction from a validated PDF/DOCX (docs/adr/0011-resume-import.md "PDF
extraction", "DOCX extraction"). Never OCR: a scanned/image-only PDF is detected and reported as
`TextNotExtractableError`, never silently presented as a successful (empty) extraction
(docs/adr/0011-...md "scanned PDF / OCR").
"""

import io

import docx
import pypdf
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf.errors import PdfReadError

from tapjob_api.resume_import.errors import (
    DocumentTooLargeError,
    EncryptedDocumentError,
    MalformedDocumentError,
    TextNotExtractableError,
)
from tapjob_api.resume_import.limits import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_PDF_PAGE_CONTENT_STREAM_BYTES,
    MAX_PDF_PAGE_TEXT_CHARS,
    MAX_PDF_PAGES,
)

# A real résumé almost always has more non-whitespace characters than this once it has a genuine
# text layer; a scanned/image-only PDF typically yields only incidental embedded-font artifacts or
# nothing at all. Conservative on purpose -- a false "needs OCR" is a safe failure, a false
# "extracted successfully" from a blank page is not (docs/adr/0011-...md "scanned PDF / OCR").
_MIN_MEANINGFUL_TEXT_CHARS = 40


def _page_content_stream_size(page: "pypdf.PageObject") -> int:
    """Round-1 independent review "Finding 6": measures a page's own DECODED content-stream size
    BEFORE `extract_text()`'s own (more expensive, font/layout-aware) parsing ever runs on it.

    Honest limitation, investigated and confirmed against this pinned pypdf version (6.19.0): a
    PDF content stream is virtually always `FlateDecode`-compressed, and pypdf's own object model
    does not expose the COMPRESSED byte length without resolving the stream at all (`/Length` is
    consumed internally while parsing the object graph and is not retained as a readable
    attribute on the materialized stream object in this version) -- there is no public, version-
    stable way to bound the stream's cost purely from its compressed size alone. `get_contents()`
    itself already performs the (lazy, cached by pypdf) decode internally to build its parsed
    `ContentStream` representation, so THIS measurement's own cost is not free -- it is deliberately
    a separate, explicit, NAMED gate run once per page, immediately before the decision whether to
    call `extract_text()` at all, rather than relying on `extract_text()`'s own unbounded internal
    behavior to discover a problem. A genuinely malicious, deeply-nested decompression bomb that
    exhausts memory DURING this measurement itself is a known residual risk this check does not
    close -- closing it fully would require parsing the raw PDF byte stream's xref/object table by
    hand, bypassing pypdf's object model entirely, which `docs/adr/0011-resume-import.md` records
    as an explicit, deliberate non-goal for this checkpoint, mitigated instead by the overall
    `MAX_UPLOAD_BYTES` (10 MiB) upload cap: PDF content streams use a single level of `FlateDecode`
    compression (not a recursively-nested archive format like a ZIP bomb achieves), so the maximum
    possible expansion from a 10 MiB upload is large but mathematically bounded, never unbounded."""
    contents = page.get_contents()
    if contents is None:
        return 0
    return len(contents.get_data())


def extract_pdf_text(content: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except PdfReadError as error:
        raise MalformedDocumentError("could not parse PDF structure") from error

    if reader.is_encrypted:
        # pypdf can sometimes open an encrypted PDF with an empty owner password and still read
        # pages; TapJob never attempts ANY password, including a blank one, on a document the user
        # did not explicitly mark as unprotected -- treat any encrypted PDF as locked.
        raise EncryptedDocumentError("password-protected PDF")

    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        raise DocumentTooLargeError(f"PDF exceeds {MAX_PDF_PAGES} pages")

    chunks: list[str] = []
    total_chars = 0
    for page in reader.pages:
        # Checked BEFORE this page's own (potentially expensive -- pypdf's own documented
        # per-content-stream memory warning) `extract_text()` call, not after: once the overall
        # character budget is already exhausted by an earlier page, every later page must be
        # skipped WITHOUT ever running its own extraction, never merely have its (already fully
        # computed) output discarded (docs/adr/0011-...md "PDF memory defense" / "resource bound
        # placement").
        if total_chars >= MAX_EXTRACTED_TEXT_CHARS:
            break
        try:
            stream_size = _page_content_stream_size(page)
        except Exception as error:
            raise MalformedDocumentError("could not inspect a PDF page's content stream") from error
        if stream_size > MAX_PDF_PAGE_CONTENT_STREAM_BYTES:
            # The minimum bar this finding requires: a stream above the bound must NEVER reach
            # `extract_text()` at all, regardless of how expensive measuring it already was.
            raise DocumentTooLargeError(
                f"a PDF page's content stream exceeds {MAX_PDF_PAGE_CONTENT_STREAM_BYTES} bytes"
            )
        try:
            page_text = page.extract_text() or ""
        except Exception as error:
            # pypdf's own extraction can raise several internal exception types for a pathological
            # content stream; all of them become the same normalized failure
            # (docs/ENGINEERING_STANDARDS.md §13-14), never a raw traceback.
            raise MalformedDocumentError("could not extract text from a PDF page") from error
        # Bounds a single pathological page's own contribution before it is ever appended --
        # `extract_text()` itself already ran for THIS page (pypdf offers no finer-grained,
        # bounded-output extraction API), but this keeps one abusive page's output from
        # dominating the shared budget every subsequent page is checked against above.
        bounded_page_text = page_text[:MAX_PDF_PAGE_TEXT_CHARS]
        remaining = MAX_EXTRACTED_TEXT_CHARS - total_chars
        chunk = bounded_page_text[:remaining]
        chunks.append(chunk)
        total_chars += len(chunk)

    text = "\n".join(chunks)
    if len(text.strip()) < _MIN_MEANINGFUL_TEXT_CHARS:
        raise TextNotExtractableError("no meaningful text layer -- OCR required")
    return text


def extract_docx_text(content: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as error:
        # python-docx raises several underlying exception types (including from its own lxml/zip
        # handling) for a malformed package; all of them become the same normalized failure here.
        raise MalformedDocumentError("could not parse DOCX structure") from error

    pieces: list[str] = []
    total_chars = 0

    def _append(text: str) -> bool:
        """Returns False once the bound is reached, signalling the caller to stop walking the
        document entirely (docs/adr/0011-...md "bound extraction")."""
        nonlocal total_chars
        if not text.strip():
            return True
        remaining = MAX_EXTRACTED_TEXT_CHARS - total_chars
        if remaining <= 0:
            return False
        bounded = text[:remaining]
        pieces.append(bounded)
        total_chars += len(bounded)
        return total_chars < MAX_EXTRACTED_TEXT_CHARS

    # Paragraphs AND tables, in document order, where python-docx's own `document.element.body`
    # iteration makes that straightforward -- a résumé frequently puts skills/dates in table layout
    # (docs/adr/0011-...md "DOCX extraction": "do not assume paragraphs alone contain all content").
    body = document.element.body
    for child in body.iterchildren():
        if not _append(""):
            break
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            if not _append(paragraph.text):
                break
        elif tag == "tbl":
            table = Table(child, document)
            for row in table.rows:
                row_text = " | ".join(cell.text for cell in row.cells)
                if not _append(row_text):
                    break

    text = "\n".join(pieces)
    if len(text.strip()) < _MIN_MEANINGFUL_TEXT_CHARS:
        raise TextNotExtractableError("DOCX contains no meaningful text content")
    return text
