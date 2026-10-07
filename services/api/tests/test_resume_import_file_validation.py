"""Unit tests for `tapjob_api.resume_import.file_validation` (docs/adr/0011-resume-import.md "file
security", "upload boundary"). Pure, no database/network.
"""

import io
import zipfile

import pytest

from tapjob_api.resume_import.errors import (
    DocumentTooLargeError,
    EncryptedDocumentError,
    MalformedDocumentError,
    UnsupportedDocumentTypeError,
)
from tapjob_api.resume_import.file_validation import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    DocumentKind,
    validate_upload,
)
from tapjob_api.resume_import.limits import MAX_UPLOAD_BYTES


def _minimal_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF"


def _minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


def test_a_real_pdf_signature_is_accepted() -> None:
    result = validate_upload(
        content=_minimal_pdf_bytes(), declared_content_type=PDF_MEDIA_TYPE, filename="resume.pdf"
    )
    assert result.kind is DocumentKind.PDF
    assert result.media_type == PDF_MEDIA_TYPE


def test_a_real_docx_container_is_accepted() -> None:
    result = validate_upload(
        content=_minimal_docx_bytes(), declared_content_type=DOCX_MEDIA_TYPE, filename="resume.docx"
    )
    assert result.kind is DocumentKind.DOCX
    assert result.media_type == DOCX_MEDIA_TYPE


def test_declared_pdf_without_the_real_signature_is_rejected() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=b"not actually a pdf" * 10,
            declared_content_type=PDF_MEDIA_TYPE,
            filename="resume.pdf",
        )


def test_content_type_alone_is_never_trusted() -> None:
    """A `.docx`-looking filename/Content-Type over a real PDF body is rejected, not silently
    accepted as whichever type the client claimed (docs/adr/0011-...md "do NOT trust Content-Type
    alone")."""
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=_minimal_pdf_bytes(),
            declared_content_type=DOCX_MEDIA_TYPE,
            filename="resume.docx",
        )


def test_an_unsupported_format_is_rejected_regardless_of_filename() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=b"plain text content, not a real document" * 5,
            declared_content_type="text/plain",
            filename="resume.txt",
        )


def test_empty_upload_is_rejected() -> None:
    with pytest.raises(MalformedDocumentError):
        validate_upload(content=b"", declared_content_type=PDF_MEDIA_TYPE, filename="resume.pdf")


def test_oversized_upload_is_rejected() -> None:
    oversized = _minimal_pdf_bytes() + b"\x00" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(DocumentTooLargeError):
        validate_upload(content=oversized, declared_content_type=PDF_MEDIA_TYPE, filename="r.pdf")


def test_a_legacy_ole_compound_file_is_reported_as_encrypted() -> None:
    """A password-protected/encrypted legacy-container DOCX is an OLE compound file, not a ZIP --
    detected explicitly rather than falling through to a confusing "unsupported type"
    (docs/adr/0011-...md "password-protected / encrypted documents")."""
    ole_signature = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    with pytest.raises(EncryptedDocumentError):
        validate_upload(
            content=ole_signature + b"\x00" * 100,
            declared_content_type=DOCX_MEDIA_TYPE,
            filename="resume.docx",
        )


def test_a_malformed_zip_declared_as_docx_is_rejected() -> None:
    with pytest.raises(MalformedDocumentError):
        validate_upload(
            content=b"PK\x03\x04" + b"garbage, not a real zip central directory" * 3,
            declared_content_type=DOCX_MEDIA_TYPE,
            filename="resume.docx",
        )


def test_a_zip_missing_required_docx_members_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("not_a_docx_member.txt", "hello")
    with pytest.raises(MalformedDocumentError):
        validate_upload(
            content=buffer.getvalue(), declared_content_type=DOCX_MEDIA_TYPE, filename="r.docx"
        )


def test_a_zip_with_too_many_members_is_rejected_as_too_large() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", "<document/>")
        for index in range(100):
            archive.writestr(f"extra/padding_{index}.bin", "x")
    with pytest.raises(DocumentTooLargeError):
        validate_upload(
            content=buffer.getvalue(), declared_content_type=DOCX_MEDIA_TYPE, filename="r.docx"
        )


def test_an_allowlisted_extension_cannot_be_bypassed_by_a_matching_real_signature() -> None:
    """Round-1 independent review "Finding 7": a prior signature-only fallback accepted a `.txt`-
    named upload whose body happened to be a real PDF, bypassing the extension allowlist entirely.
    Extension is now a hard, independent gate -- a real PDF body behind a non-`.pdf`/`.docx`
    filename is rejected regardless of what its bytes look like."""
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=_minimal_pdf_bytes(),
            declared_content_type="application/octet-stream",
            filename="resume.txt",
        )
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=_minimal_docx_bytes(),
            declared_content_type="application/octet-stream",
            filename="resume.bin",
        )


def test_pdf_filename_with_a_real_docx_body_is_rejected() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=_minimal_docx_bytes(), declared_content_type=PDF_MEDIA_TYPE, filename="r.pdf"
        )


def test_docx_filename_with_a_real_pdf_body_is_rejected() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        validate_upload(
            content=_minimal_pdf_bytes(), declared_content_type=DOCX_MEDIA_TYPE, filename="r.docx"
        )


def test_a_generic_content_type_is_accepted_when_extension_and_body_both_agree() -> None:
    """Content-Type is advisory only -- a generic/wrong Content-Type never blocks an upload whose
    extension and real signature both genuinely agree (docs/adr/0011-...md "do NOT trust
    Content-Type alone" cuts both ways: it cannot promote OR demote acceptance)."""
    pdf_result = validate_upload(
        content=_minimal_pdf_bytes(),
        declared_content_type="application/octet-stream",
        filename="resume.pdf",
    )
    assert pdf_result.kind is DocumentKind.PDF

    docx_result = validate_upload(
        content=_minimal_docx_bytes(),
        declared_content_type="application/octet-stream",
        filename="resume.docx",
    )
    assert docx_result.kind is DocumentKind.DOCX


def test_a_zip_bomb_style_high_compression_ratio_is_rejected() -> None:
    """A single member whose declared uncompressed size vastly exceeds its compressed size is
    rejected from ZIP METADATA ALONE, before any member is ever decompressed
    (docs/adr/0011-...md "inspect ZIP metadata BEFORE expensive decompression")."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        # Highly compressible, large payload -- a real zip bomb pattern (repeated byte).
        archive.writestr("word/document.xml", b"\x00" * (5 * 1024 * 1024))
    with pytest.raises(DocumentTooLargeError):
        validate_upload(
            content=buffer.getvalue(), declared_content_type=DOCX_MEDIA_TYPE, filename="r.docx"
        )
