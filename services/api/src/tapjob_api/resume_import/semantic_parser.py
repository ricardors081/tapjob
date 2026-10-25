"""Semantic resume parser implementation behind the ResumeParser port.

Uses AiGateway (with AiPurpose.RESUME_EXTRACTION) to extract structured facts
from whole resume documents.
- Treats the whole document as UntrustedContent
- Validates that source excerpts are grounded in actual document text
- Distinguishes candidate name from professional headline
- Deduplicates skills while preserving provenance
- Implements ResumeParser protocol (provider = "semantic-openai-v1")
"""

from __future__ import annotations

import logging
import re
from typing import Any

from tapjob_api.ports._common import AdapterHealth, CallContext
from tapjob_api.ports.ai_gateway import (
    AiGateway,
    AiPurpose,
    AiRequest,
    PromptRef,
    UntrustedContent,
)
from tapjob_api.ports.capabilities import CapabilityManifest, CapabilityReport, CapabilityStatus
from tapjob_api.ports.resume_parser import (
    ExtractedFact,
    ParsedResume,
    ResumeDocument,
    ResumeParserCapability,
)
from tapjob_api.resume_import.errors import TextNotExtractableError
from tapjob_api.resume_import.extraction import extract_docx_text, extract_pdf_text
from tapjob_api.resume_import.file_validation import PDF_MEDIA_TYPE
from tapjob_api.resume_import.limits import MAX_CANDIDATES_PER_IMPORT, MAX_SOURCE_EXCERPT_CHARS
from tapjob_api.resume_import.semantic_schema import (
    SemanticResumeExtraction,
)

logger = logging.getLogger(__name__)

SEMANTIC_PARSER_IDENTITY = "semantic-openai-v1"

_NORMALIZE_WS = re.compile(r"\s+")


def _normalize_text_for_search(text: str) -> str:
    """Normalizes whitespace and casing to verify excerpt presence."""
    return _NORMALIZE_WS.sub(" ", text).strip().lower()


def _is_excerpt_grounded(excerpt: str | None, full_text: str) -> bool:
    if not excerpt or not excerpt.strip():
        return False
    clean_excerpt = _normalize_text_for_search(excerpt)
    if not clean_excerpt or len(clean_excerpt) < 3:
        return False
    clean_full = _normalize_text_for_search(full_text)
    return clean_excerpt in clean_full


class SemanticResumeParser:
    """AiGateway-backed semantic resume parser implementing the ResumeParser protocol."""

    def __init__(
        self,
        ai_gateway: AiGateway,
        *,
        provider_identity: str = SEMANTIC_PARSER_IDENTITY,
    ) -> None:
        self.provider = provider_identity
        self._ai_gateway = ai_gateway

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[ResumeParserCapability]:
        return CapabilityManifest(
            [
                CapabilityReport(
                    capability=ResumeParserCapability.STRUCTURED_EXTRACTION,
                    status=CapabilityStatus.SUPPORTED,
                )
            ]
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return await self._ai_gateway.health(ctx)

    async def parse(self, document: ResumeDocument, ctx: CallContext) -> ParsedResume:
        # Extract full raw text
        if document.media_type == PDF_MEDIA_TYPE:
            text = extract_pdf_text(document.content)
        elif document.media_type.startswith("text/") or document.media_type == "text/plain":
            text = document.content.decode("utf-8", errors="replace")
        else:
            text = extract_docx_text(document.content)

        if not text or not text.strip():
            raise TextNotExtractableError("No text extractable from document")

        # Invoke AiGateway with RESUME_EXTRACTION purpose and whole document as UntrustedContent
        request = AiRequest[SemanticResumeExtraction](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="resume_semantic_extraction", version="1.0"),
            variables={"document_filename": document.filename},
            untrusted=(UntrustedContent(source_kind="resume_document", content=text),),
            grounding=(),
            validate_output=SemanticResumeExtraction.model_validate,
        )

        ai_result = await self._ai_gateway.generate(request, ctx)
        extraction: SemanticResumeExtraction = ai_result.output

        facts = self._map_extraction_to_facts(extraction, original_text=text)
        return ParsedResume(facts=tuple(facts))

    def _map_extraction_to_facts(
        self, extraction: SemanticResumeExtraction, *, original_text: str
    ) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        candidate_name = extraction.candidate_name.strip() if extraction.candidate_name else None

        # 1. Headline (ensure distinction from candidate name)
        if extraction.professional_headline and extraction.professional_headline.headline:
            headline_text = extraction.professional_headline.headline.strip()
            # If headline is identical to candidate name or trivial name, reject as headline
            is_name_confusion = False
            if (candidate_name and headline_text.lower() == candidate_name.lower()) or (
                len(headline_text.split()) <= 3
                and any(
                    part.lower() in (candidate_name or "").lower() for part in headline_text.split()
                )
                and not any(
                    kw in headline_text.lower()
                    for kw in (
                        "engineer",
                        "analyst",
                        "developer",
                        "manager",
                        "lead",
                        "specialist",
                        "pmo",
                        "data",
                        "ai",
                        "director",
                        "consultant",
                    )
                )
            ):
                is_name_confusion = True

            if not is_name_confusion:
                excerpt = extraction.professional_headline.source_excerpt
                if not _is_excerpt_grounded(excerpt, original_text):
                    excerpt = None
                facts.append(
                    ExtractedFact(
                        fact_type="headline",
                        value={"kind": "headline", "text": headline_text},
                        confidence=extraction.professional_headline.confidence,
                        source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                    )
                )

        # 2. Professional Summary
        if extraction.professional_summary and extraction.professional_summary.summary:
            summary_text = extraction.professional_summary.summary.strip()
            excerpt = extraction.professional_summary.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            facts.append(
                ExtractedFact(
                    fact_type="summary",
                    value={"kind": "summary", "text": summary_text},
                    confidence=extraction.professional_summary.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 3. Experiences
        for exp in extraction.experiences:
            if not exp.employer or not exp.title:
                continue
            excerpt = exp.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None

            val: dict[str, Any] = {
                "kind": "experience",
                "employer": exp.employer.strip(),
                "title": exp.title.strip(),
                "isCurrent": exp.is_current,
            }
            if exp.location:
                val["location"] = exp.location.strip()
            if exp.start_year is not None:
                val["start"] = {
                    "year": exp.start_year,
                    "month": exp.start_month
                    if exp.start_month and 1 <= exp.start_month <= 12
                    else None,
                }
            if exp.end_year is not None and not exp.is_current:
                val["end"] = {
                    "year": exp.end_year,
                    "month": exp.end_month if exp.end_month and 1 <= exp.end_month <= 12 else None,
                }
            if exp.description:
                val["description"] = exp.description.strip()
            if exp.achievements:
                val["achievements"] = [a.strip() for a in exp.achievements if a.strip()]
            if exp.technologies:
                val["technologies"] = [t.strip() for t in exp.technologies if t.strip()]

            facts.append(
                ExtractedFact(
                    fact_type="experience",
                    value=val,
                    confidence=exp.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 4. Education
        for edu in extraction.education:
            if not edu.institution:
                continue
            excerpt = edu.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None

            val = {
                "kind": "education",
                "institution": edu.institution.strip(),
            }
            if edu.degree:
                val["degree"] = edu.degree.strip()
            if edu.field_of_study:
                val["fieldOfStudy"] = edu.field_of_study.strip()
            if edu.start_year is not None:
                val["start"] = {"year": edu.start_year, "month": None}
            if edu.end_year is not None:
                val["end"] = {"year": edu.end_year, "month": None}
            if edu.status:
                st = edu.status.strip().lower()
                if st in ("concluído", "concluido", "completed"):
                    val["status"] = "completed"
                elif st in ("em andamento", "cursando", "in_progress", "in progress"):
                    val["status"] = "in_progress"
                elif st in ("incompleto", "incomplete"):
                    val["status"] = "incomplete"
            if edu.description:
                val["description"] = edu.description.strip()

            facts.append(
                ExtractedFact(
                    fact_type="education",
                    value=val,
                    confidence=edu.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 5. Skills (deduplicating case-insensitively and preserving provenance)
        seen_skills: set[str] = set()
        skill_facts: list[ExtractedFact] = []

        # From explicit skills
        for sk in extraction.skills:
            if not sk.name or not sk.name.strip():
                continue
            normalized_name = sk.name.strip()
            key = normalized_name.lower()
            if key in seen_skills:
                continue
            seen_skills.add(key)
            excerpt = sk.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            skill_facts.append(
                ExtractedFact(
                    fact_type="skill",
                    value={"kind": "skill", "name": normalized_name},
                    confidence=sk.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # Also pull technologies from experiences and projects if not already added
        for exp in extraction.experiences:
            for tech in exp.technologies:
                if not tech or not tech.strip():
                    continue
                normalized_tech = tech.strip()
                key = normalized_tech.lower()
                if key in seen_skills:
                    continue
                seen_skills.add(key)
                excerpt = exp.source_excerpt
                if not _is_excerpt_grounded(excerpt, original_text):
                    excerpt = None
                skill_facts.append(
                    ExtractedFact(
                        fact_type="skill",
                        value={"kind": "skill", "name": normalized_tech},
                        confidence=0.9,
                        source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                    )
                )

        facts.extend(skill_facts)

        # 6. Certifications
        for cert in extraction.certifications:
            if not cert.name or not cert.name.strip():
                continue
            excerpt = cert.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            val = {
                "kind": "certification",
                "name": cert.name.strip(),
            }
            if cert.issuer:
                val["issuer"] = cert.issuer.strip()
            if cert.issue_year is not None:
                val["issued"] = {
                    "year": cert.issue_year,
                    "month": cert.issue_month
                    if cert.issue_month and 1 <= cert.issue_month <= 12
                    else None,
                }
            facts.append(
                ExtractedFact(
                    fact_type="certification",
                    value=val,
                    confidence=cert.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 7. Languages
        for lang in extraction.languages:
            if not lang.language or not lang.language.strip():
                continue
            excerpt = lang.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            val = {
                "kind": "language",
                "language": lang.language.strip(),
            }
            if lang.proficiency:
                val["proficiency"] = lang.proficiency.strip()
            facts.append(
                ExtractedFact(
                    fact_type="language",
                    value=val,
                    confidence=lang.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 8. Projects
        for proj in extraction.projects:
            if not proj.name or not proj.name.strip():
                continue
            excerpt = proj.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            val = {
                "kind": "project",
                "name": proj.name.strip(),
            }
            if proj.description:
                val["description"] = proj.description.strip()
            if proj.role:
                val["role"] = proj.role.strip()
            if proj.url:
                val["url"] = proj.url.strip()
            if proj.technologies:
                val["technologies"] = [t.strip() for t in proj.technologies if t.strip()]
            facts.append(
                ExtractedFact(
                    fact_type="project",
                    value=val,
                    confidence=proj.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 9. Publications
        for pub in extraction.publications:
            if not pub.title or not pub.title.strip():
                continue
            excerpt = pub.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            val = {
                "kind": "publication",
                "title": pub.title.strip(),
            }
            if pub.publisher:
                val["publisher"] = pub.publisher.strip()
            if pub.date:
                val["date"] = pub.date.strip()
            if pub.url:
                val["url"] = pub.url.strip()
            if pub.description:
                val["description"] = pub.description.strip()
            facts.append(
                ExtractedFact(
                    fact_type="publication",
                    value=val,
                    confidence=pub.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        # 10. Links
        for link in extraction.links:
            if not link.url or not link.url.strip():
                continue
            excerpt = link.source_excerpt
            if not _is_excerpt_grounded(excerpt, original_text):
                excerpt = None
            val = {
                "kind": "link",
                "url": link.url.strip(),
                "linkType": "portfolio",
                "label": (link.label or "Portfolio").strip(),
            }
            facts.append(
                ExtractedFact(
                    fact_type="link",
                    value=val,
                    confidence=link.confidence,
                    source_excerpt=(excerpt or "")[:MAX_SOURCE_EXCERPT_CHARS] or None,
                )
            )

        return facts[:MAX_CANDIDATES_PER_IMPORT]
