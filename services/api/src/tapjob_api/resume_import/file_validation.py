"""Upload/container security validation (docs/adr/0011-resume-import.md "file security",
"upload boundary"). Content-Type/extension are never trusted alone -- every accepted document is
sniffed by its real binary signature/container structure before anything downstream (storage,
extraction) ever touches it (docs/SECURITY_PRIVACY.md §12).
"""

import io
import zipfile
from dataclasses import dataclass
from enum import StrEnum

from tapjob_api.resume_import.errors import (
    DocumentTooLargeError,
    EncryptedDocumentError,
    MalformedDocumentError,
    UnsupportedDocumentTypeError,
)
from tapjob_api.resume_import.limits import (
    MAX_DOCX_COMPRESSION_RATIO,
    MAX_DOCX_UNCOMPRESSED_BYTES,
    MAX_DOCX_ZIP_MEMBERS,
    MAX_UPLOAD_BYTES,
)

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PDF_SIGNATURE = b"%PDF-"
# A real-world PDF's header is occasionally preceded by a small amount of leading junk (some
# generators prepend a BOM or a few bytes); tools commonly scan the first 1024 bytes rather than
# requiring byte 0 exactly.
_PDF_SIGNATURE_SCAN_WINDOW = 1024
_ZIP_LOCAL_FILE_SIGNATURE = b"PK\x03\x04"
_REQUIRED_DOCX_MEMBERS = frozenset({"[Content_Types].xml", "word/document.xml", "_rels/.rels"})
# Legacy OLE/CFBF container signature -- a password-protected/encrypted DOCX is wrapped in this
# format (MS-OFFCRYPTO), not a plain ZIP, so it fails `_looks_like_zip` outright. Detected
# explicitly here so it surfaces as `EncryptedDocumentError`, not a confusing "unsupported type".
_OLE_COMPOUND_FILE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class DocumentKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    kind: DocumentKind
    media_type: str


def _looks_like_pdf(content: bytes) -> bool:
    return _PDF_SIGNATURE in content[:_PDF_SIGNATURE_SCAN_WINDOW]


def _looks_like_zip(content: bytes) -> bool:
    return content[:4] == _ZIP_LOCAL_FILE_SIGNATURE


def validate_docx_container(content: bytes) -> None:
    """Inspects ZIP metadata ONLY (member count, declared uncompressed size, compression ratio)
    BEFORE any member is actually decompressed (docs/adr/0011-...md "inspect ZIP metadata before
    expensive decompression") -- `ZipInfo.file_size`/`compress_size` come from the archive's central
    directory, not from reading/decompressing the member itself."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_ZIP_MEMBERS:
                raise DocumentTooLargeError("too many ZIP members")
            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise DocumentTooLargeError("uncompressed size exceeds limit")
            total_compressed = sum(info.compress_size for info in infos) or 1
            if total_uncompressed / total_compressed > MAX_DOCX_COMPRESSION_RATIO:
                raise DocumentTooLargeError("compression ratio exceeds limit (zip-bomb heuristic)")
            names = frozenset(archive.namelist())
            if not _REQUIRED_DOCX_MEMBERS.issubset(names):
                raise MalformedDocumentError("missing required DOCX/OPC members")
    except zipfile.BadZipFile as error:
        raise MalformedDocumentError("not a well-formed ZIP/OPC container") from error


def validate_upload(
    *, content: bytes, declared_content_type: str, filename: str
) -> ValidatedUpload:
    """The one entry point `service.py` calls before anything is stored or parsed.

    Two independent gates, BOTH required (round-1 independent review "Finding 7" -- a signature-
    only fallback previously let a `.txt`-named upload with a real PDF body through, bypassing the
    extension allowlist entirely):

    1. `filename`'s extension must be `.pdf` or `.docx` -- this is the allowlist gate. Anything
       else is rejected outright, regardless of what its bytes look like.
    2. The content's real binary signature/container structure must then match THAT declared kind.
       A `.pdf`-named upload whose bytes are not a real PDF (e.g. a `.docx`-renamed-to-`.pdf`, or
       a plain text file) is rejected; same for `.docx`.

    `declared_content_type` (the client's `Content-Type` header) is NEVER part of either gate --
    it is pure advisory metadata TapJob does not trust at all, never a way to make an extension
    mean something other than what it says (docs/adr/0011-...md "do NOT trust Content-Type alone").
    `filename` is used only for its extension -- it is NEVER used to derive a storage path
    (docs/adr/0011-...md "never make the original filename the storage key")."""
    del declared_content_type  # advisory only -- never influences accept/reject, see docstring
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentTooLargeError("upload exceeds the maximum allowed size")
    if len(content) == 0:
        raise MalformedDocumentError("empty upload")

    if content[:8] == _OLE_COMPOUND_FILE_SIGNATURE:
        raise EncryptedDocumentError("password-protected/encrypted legacy-container document")

    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        if not _looks_like_pdf(content):
            raise UnsupportedDocumentTypeError(
                "a .pdf upload's content does not match a real PDF signature"
            )
        return ValidatedUpload(kind=DocumentKind.PDF, media_type=PDF_MEDIA_TYPE)
    if lower_name.endswith(".docx"):
        if not _looks_like_zip(content):
            raise UnsupportedDocumentTypeError(
                "a .docx upload's content does not match a real ZIP/OPC signature"
            )
        validate_docx_container(content)
        return ValidatedUpload(kind=DocumentKind.DOCX, media_type=DOCX_MEDIA_TYPE)

    raise UnsupportedDocumentTypeError("only .pdf and .docx filenames are accepted")
