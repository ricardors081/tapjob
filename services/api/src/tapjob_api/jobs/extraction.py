"""HTML extraction and structured job parsing for URL intake (WP2.1-A)."""

import json
import re
from dataclasses import dataclass

from tapjob_api.jobs.errors import JobPageRequiresManualPasteError
from tapjob_api.jobs.normalization import clean_description_text

_TITLE_SEPARATOR_RE = re.compile(r"\s+[-|–—•]\s+|\s+at\s+", re.IGNORECASE)  # noqa: RUF001
_META_PROPERTY_RE = re.compile(
    r'<meta\s+[^>]*?(?:property|name)=["\']([^"\']+)["\'][^>]*?content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_TAG_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_JSON_LD_RE = re.compile(
    r'<script\s+[^>]*?type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_JS_ONLY_PATTERNS = (
    "you need to enable javascript",
    "please enable javascript",
    "javascript is required",
    "javascript must be enabled",
    "enable javascript to continue",
    "loading job",
    "loading...",
    "carregando...",
)

_LOADING_TITLES = (
    "loading...",
    "loading",
    "carregando...",
    "carregando",
)


@dataclass(frozen=True, slots=True)
class ExtractedJobDetails:
    """Structured details extracted from an HTML job page."""

    title: str
    company_name: str
    description: str
    location: str | None = None
    is_source_declared: bool = False


def _find_job_posting_ld_json(data: object) -> dict[str, object] | None:
    """Find a JobPosting schema object recursively in parsed JSON-LD."""
    if isinstance(data, dict):
        obj_type = data.get("@type")
        if obj_type == "JobPosting" or (isinstance(obj_type, list) and "JobPosting" in obj_type):
            return data
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                found = _find_job_posting_ld_json(item)
                if found:
                    return found
    elif isinstance(data, list):
        for item in data:
            found = _find_job_posting_ld_json(item)
            if found:
                return found
    return None


def extract_job_from_html(html_text: str) -> ExtractedJobDetails:
    """Extract job title, company name, and description from HTML document."""
    # 1. Attempt JSON-LD Schema.org JobPosting
    for match in _JSON_LD_RE.finditer(html_text):
        raw_json = match.group(1).strip()
        try:
            parsed_json = json.loads(raw_json)
        except (json.JSONDecodeError, ValueError):
            continue

        job_posting = _find_job_posting_ld_json(parsed_json)
        if job_posting:
            title = str(job_posting.get("title") or "").strip()
            hiring_org = job_posting.get("hiringOrganization")
            company_name = ""
            if isinstance(hiring_org, dict):
                company_name = str(hiring_org.get("name") or "").strip()
            elif isinstance(hiring_org, str):
                company_name = hiring_org.strip()

            raw_desc = str(job_posting.get("description") or "").strip()
            cleaned_desc = clean_description_text(raw_desc) if raw_desc else ""

            location: str | None = None
            job_loc = job_posting.get("jobLocation")
            if isinstance(job_loc, dict):
                addr = job_loc.get("address")
                if isinstance(addr, dict):
                    locality = addr.get("addressLocality")
                    region = addr.get("addressRegion")
                    if locality and region:
                        location = f"{locality}, {region}"
                    elif locality:
                        location = str(locality)

            if title and company_name and len(cleaned_desc) >= 30:
                return ExtractedJobDetails(
                    title=title,
                    company_name=company_name,
                    description=cleaned_desc,
                    location=location,
                    is_source_declared=True,
                )

    # 2. Extract OpenGraph and standard meta tags
    meta_tags: dict[str, str] = {}
    for match in _META_PROPERTY_RE.finditer(html_text):
        prop_name = match.group(1).lower()
        content = match.group(2).strip()
        if prop_name not in meta_tags:
            meta_tags[prop_name] = content

    og_title = meta_tags.get("og:title")
    og_desc = meta_tags.get("og:description") or meta_tags.get("description")
    og_site_name = meta_tags.get("og:site_name")

    # Document title and H1 fallback
    doc_title_match = _TITLE_TAG_RE.search(html_text)
    doc_title = doc_title_match.group(1).strip() if doc_title_match else ""

    h1_match = _H1_TAG_RE.search(html_text)
    h1_title = clean_description_text(h1_match.group(1).strip()) if h1_match else ""

    candidate_title = og_title or h1_title or doc_title
    candidate_company = og_site_name or ""

    # If company not found from og:site_name, check doc_title for "Title - Company"
    if not candidate_company and doc_title and _TITLE_SEPARATOR_RE.search(doc_title):
        parts = _TITLE_SEPARATOR_RE.split(doc_title, maxsplit=1)
        if len(parts) == 2 and len(parts[0]) > 2 and len(parts[1]) > 2:
            candidate_company = parts[1].strip()
            if not og_title and not h1_title:
                candidate_title = parts[0].strip()

    # If title itself has separator and company still empty, split it
    if candidate_title and not candidate_company and _TITLE_SEPARATOR_RE.search(candidate_title):
        parts = _TITLE_SEPARATOR_RE.split(candidate_title, maxsplit=1)
        if len(parts) == 2 and len(parts[0]) > 2 and len(parts[1]) > 2:
            candidate_title = parts[0].strip()
            candidate_company = parts[1].strip()

    cleaned_body = clean_description_text(html_text)

    # Check for JavaScript-required shell
    body_lower = cleaned_body.lower()
    for pattern in _JS_ONLY_PATTERNS:
        if pattern in body_lower and len(cleaned_body) < 500:
            raise JobPageRequiresManualPasteError(
                "This page requires JavaScript execution. "
                "Please paste the job description manually."
            )

    title_lower = candidate_title.lower().strip()
    if not candidate_title or title_lower in _LOADING_TITLES or title_lower.startswith("loading"):
        raise JobPageRequiresManualPasteError(
            "This page requires JavaScript execution or does not contain a static job posting. "
            "Please paste the job description manually."
        )

    description = og_desc if og_desc and len(og_desc) >= 30 else cleaned_body

    if len(description) < 30:
        raise JobPageRequiresManualPasteError(
            "Could not extract a readable job description. "
            "Please paste the job description manually."
        )

    return ExtractedJobDetails(
        title=candidate_title[:255],
        company_name=(candidate_company or "Unknown Organization")[:255],
        description=description,
        location=None,
        is_source_declared=bool(og_title or og_desc),
    )
