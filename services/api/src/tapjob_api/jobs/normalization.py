"""Deterministic normalization and text cleanup for Jobs (WP2.1-A).

Adapted from wardoep/jobbot (SHA 5932b3782412aeda4ed492df7c788b1248121ecb).
Ported dedupe concepts from Gsync/jobsync and roysahar11/appliable.

Invariants:
- Deterministic only: no LLM, no external network calls.
- Untrusted input: prompt-injection patterns and HTML are strictly inert data.
- Never fabricates claims not present in source input.
"""

import hashlib
import html
import re
import unicodedata
from uuid import UUID

from tapjob_api.jobs.models import JobWorkplaceType

_DISALLOWED_ATS_DOMAINS = {
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "workable.com",
    "jobs.workable.com",
    "gupy.io",
    "linkedin.com",
    "indeed.com",
    "smartrecruiters.com",
    "jobs.smartrecruiters.com",
    "bamboohr.com",
    "myworkdayjobs.com",
    "workday.com",
    "taleo.net",
    "icims.com",
    "jobvite.com",
    "rippling.com",
    "notion.so",
    "notion.site",
}


def clean_corporate_domain(domain: str | None) -> str | None:
    """Normalize and validate corporate domain.

    Returns normalized domain (lowercased, stripped) only if proven corporate domain.
    Returns None if domain is None, empty, or belongs to a known multi-tenant ATS/aggregator/host.
    """
    if not domain or not domain.strip():
        return None
    cleaned = domain.strip().lower()
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0].strip()
    if not cleaned:
        return None
    for ats in _DISALLOWED_ATS_DOMAINS:
        if cleaned == ats or cleaned.endswith(f".{ats}"):
            return None
    return cleaned


_WHITESPACE_RE = re.compile(r"\s+")
_STRIP_PUNCTUATION_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_TAG_BLOCK_RE = re.compile(
    r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG_NEWLINE_RE = re.compile(r"<(br|p|div|tr|h[1-6])[^>]*>", re.IGNORECASE)
_TAG_LI_RE = re.compile(r"<li[^>]*>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_CONSECUTIVE_NEWLINES_RE = re.compile(r"\n{3,}")

_ROLE_NOISE_RE = re.compile(
    r"\s*(\((m/f/d|m/w/d|h/f|m/f|h/m|remoto|remote|h[ií]brido|hybrid|presencial)\)|\[(remoto|remote|h[ií]brido|hybrid)\])\s*",
    re.IGNORECASE,
)

_COMPANY_SUFFIXES = (
    ", inc.",
    " inc.",
    ", inc",
    " inc",
    ", llc",
    " llc",
    ", ltd.",
    " ltd.",
    ", ltd",
    " ltd",
    ", ltda.",
    " ltda.",
    ", ltda",
    " ltda",
    ", s.a.",
    " s.a.",
    ", sa",
    " sa",
    " eireli",
    " me",
    " gmbh",
    " corp.",
    " corp",
)

_HYBRID_RE = re.compile(
    r"\b(h[ií]brido|hybrid|dias\s*presenciais|modelo\s*h[ií]brido)\b", re.IGNORECASE
)
_REMOTE_RE = re.compile(
    r"\b(remoto|remote|home[\s-]office|teletrabalho|anywhere|100%\s*remoto|trabalho\s*remoto)\b",
    re.IGNORECASE,
)
_ONSITE_RE = re.compile(r"\b(presencial|on[\s-]site|in[\s-]office|in[\s-]person)\b", re.IGNORECASE)


def normalize_company_name(name: str) -> str:
    """Normalize company name to canonical lowercase form with common legal suffixes stripped."""
    text = unicodedata.normalize("NFKC", name).strip().lower()
    for suffix in _COMPANY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    text = _STRIP_PUNCTUATION_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_job_title(title: str) -> str:
    """Normalize job title removing role annotations and standardizing whitespace."""
    text = unicodedata.normalize("NFKC", title).strip()
    text = _ROLE_NOISE_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def normalize_location(location: str | None) -> str | None:
    """Normalize location string if provided."""
    if not location or not location.strip():
        return None
    text = unicodedata.normalize("NFKC", location).strip().lower()
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def clean_description_text(raw_text: str) -> str:
    """Convert HTML or plain text into clean, inert plain text preserving semantic structure."""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    # If HTML tags detected, strip scripts/nav and convert tags to newlines/bullets
    if "<" in text and ">" in text:
        text = _TAG_BLOCK_RE.sub("", text)
        text = _TAG_LI_RE.sub("\n- ", text)
        text = _TAG_NEWLINE_RE.sub("\n", text)
        text = _ANY_TAG_RE.sub("", text)
        text = html.unescape(text)

    # Normalize unicode
    text = unicodedata.normalize("NFKC", text)

    # Clean lines
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned = "\n".join(lines)
    cleaned = _CONSECUTIVE_NEWLINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def classify_workplace_type(
    title: str,
    description: str,
    location: str | None = None,
    workplace_hint: str | None = None,
) -> JobWorkplaceType:
    """Classify workplace type from explicit provider hint, title, location,
    and description keywords.
    """
    if workplace_hint and workplace_hint.strip():
        hint_clean = workplace_hint.strip().lower()
        if _HYBRID_RE.search(hint_clean) or "hybrid" in hint_clean or "híbrido" in hint_clean:
            return JobWorkplaceType.HYBRID
        if _REMOTE_RE.search(hint_clean) or "remote" in hint_clean or "remoto" in hint_clean:
            return JobWorkplaceType.REMOTE
        onsite_terms = (
            "on-site",
            "onsite",
            "in-office",
            "inoffice",
            "in-person",
            "inperson",
            "presencial",
        )
        if _ONSITE_RE.search(hint_clean) or any(k in hint_clean for k in onsite_terms):
            return JobWorkplaceType.ON_SITE

    combined_header = f"{title} {location or ''}".lower()

    # Title/location has higher precedence
    if _HYBRID_RE.search(combined_header):
        return JobWorkplaceType.HYBRID
    if _REMOTE_RE.search(combined_header):
        return JobWorkplaceType.REMOTE
    if _ONSITE_RE.search(combined_header):
        return JobWorkplaceType.ON_SITE

    # Check description text
    desc_lower = description.lower()
    if _HYBRID_RE.search(desc_lower):
        return JobWorkplaceType.HYBRID
    if _REMOTE_RE.search(desc_lower):
        return JobWorkplaceType.REMOTE
    if _ONSITE_RE.search(desc_lower):
        return JobWorkplaceType.ON_SITE

    return JobWorkplaceType.UNKNOWN


def compute_similarity_fingerprint(
    company_id: UUID,
    title_normalized: str,
    location_normalized: str | None,
    description_cleaned: str,
) -> str:
    """Compute non-unique similarity fingerprint for duplicate candidate detection."""
    desc_hash = hashlib.sha256(description_cleaned.encode("utf-8")).hexdigest()
    composite = f"{company_id}:{title_normalized}:{location_normalized or ''}:{desc_hash}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()
