"""Unit tests for `tapjob_api.resume_import.extraction` (docs/adr/0011-resume-import.md "PDF
extraction", "DOCX extraction", "scanned PDF / OCR", "password-protected / encrypted documents").
Builds small, real PDF/DOCX documents in-memory with `pypdf`/`python-docx` -- never reads a
committed binary fixture.
"""

import io

import docx
import pypdf
import pytest

from tapjob_api.resume_import.errors import (
    DocumentTooLargeError,
    EncryptedDocumentError,
    TextNotExtractableError,
)
from tapjob_api.resume_import.extraction import extract_docx_text, extract_pdf_text
from tapjob_api.resume_import.limits import MAX_PDF_PAGE_CONTENT_STREAM_BYTES, MAX_PDF_PAGES


def _pdf_with_text(text: str, *, pages: int = 1) -> bytes:
    # pypdf has no built-in "write text to a page" helper without reportlab; the structural unit
    # under test here (`extract_pdf_text`) is exercised against pypdf's OWN extraction of a page
    # that genuinely has no text layer, proving the OCR-required path, rather than faking real
    # glyph content pypdf would need a font/content-stream builder to produce.
    del text
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _docx_with_paragraphs(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _docx_with_table(rows: list[list[str]]) -> bytes:
    document = docx.Document()
    document.add_paragraph("Skills")
    table = document.add_table(rows=len(rows), cols=len(rows[0]))
    for row_index, row in enumerate(rows):
        for col_index, cell_text in enumerate(row):
            table.cell(row_index, col_index).text = cell_text
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_a_blank_pdf_page_is_reported_as_text_not_extractable() -> None:
    """A page with no real text layer at all (the scanned/image-only case this test stands in
    for) must never be presented as a successful empty extraction
    (docs/adr/0011-...md "scanned PDF / OCR")."""
    with pytest.raises(TextNotExtractableError):
        extract_pdf_text(_pdf_with_text("irrelevant", pages=1))


def test_a_pdf_exceeding_the_page_limit_is_rejected() -> None:
    with pytest.raises(DocumentTooLargeError):
        extract_pdf_text(_pdf_with_text("x", pages=MAX_PDF_PAGES + 1))


def test_a_content_stream_exceeding_the_bound_never_reaches_extract_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-1 independent review "Finding 6": the minimum bar this finding requires -- a page
    whose own decoded content-stream size exceeds `MAX_PDF_PAGE_CONTENT_STREAM_BYTES` must NEVER
    reach `PageObject.extract_text()` at all. Instrumented via monkeypatch (never a real
    multi-megabyte fixture) to prove this directly: `extract_text` is simply never called."""
    import pypdf

    from tapjob_api.resume_import import extraction

    monkeypatch.setattr(
        extraction, "_page_content_stream_size", lambda _page: MAX_PDF_PAGE_CONTENT_STREAM_BYTES + 1
    )

    def _fail_if_called(self: object) -> str:
        raise AssertionError("extract_text must never be called for an over-bound content stream")

    monkeypatch.setattr(pypdf.PageObject, "extract_text", _fail_if_called)

    with pytest.raises(DocumentTooLargeError):
        extract_pdf_text(_pdf_with_text("irrelevant", pages=1))


def test_an_encrypted_pdf_is_rejected_never_attempting_even_a_blank_password() -> None:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="", owner_password="owner-secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(EncryptedDocumentError):
        extract_pdf_text(buffer.getvalue())


def test_docx_paragraphs_are_extracted_in_order() -> None:
    text = extract_docx_text(
        _docx_with_paragraphs(
            [
                "Jane Doe - Senior Backend Engineer",
                "Experiencia profissional com Python e PostgreSQL ao longo de muitos anos.",
                "Experience includes leading backend teams and designing distributed systems.",
            ]
        )
    )
    assert "Jane Doe" in text
    assert "Python e PostgreSQL" in text


def test_docx_table_content_is_not_silently_dropped() -> None:
    """A résumé frequently puts skills/dates in table layout -- paragraphs alone must not be
    assumed to contain all content (docs/adr/0011-...md "DOCX extraction")."""
    text = extract_docx_text(
        _docx_with_table([["Python", "Advanced"], ["PostgreSQL", "Intermediate level user"]])
    )
    assert "Python" in text
    assert "PostgreSQL" in text
    assert "Advanced" in text


def test_a_docx_with_no_meaningful_text_is_reported_as_not_extractable() -> None:
    with pytest.raises(TextNotExtractableError):
        extract_docx_text(_docx_with_paragraphs(["", "   ", ""]))
