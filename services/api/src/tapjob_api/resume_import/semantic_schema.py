"""Canonical Pydantic schema for structured semantic resume extraction.

This schema defines the strict Structured Output contract for AI-driven resume extraction.
It structures claims from untrusted resume documents into verified factual evidence.
"""

from typing import Any

from pydantic import BaseModel, Field


class SemanticHeadline(BaseModel):
    model_config = {"extra": "ignore"}

    headline: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticSummary(BaseModel):
    model_config = {"extra": "ignore"}

    summary: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticExperience(BaseModel):
    model_config = {"extra": "ignore"}

    employer: str
    title: str
    location: str | None = None
    start_year: int | None = None
    start_month: int | None = None
    end_year: int | None = None
    end_month: int | None = None
    is_current: bool = False
    description: str | None = None
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticEducation(BaseModel):
    model_config = {"extra": "ignore"}

    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    status: str | None = None
    description: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticSkill(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    category: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticCertification(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    issuer: str | None = None
    issue_year: int | None = None
    issue_month: int | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticLanguage(BaseModel):
    model_config = {"extra": "ignore"}

    language: str
    proficiency: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticProject(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    description: str | None = None
    role: str | None = None
    url: str | None = None
    technologies: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticPublication(BaseModel):
    model_config = {"extra": "ignore"}

    title: str
    publisher: str | None = None
    date: str | None = None
    url: str | None = None
    description: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticLink(BaseModel):
    model_config = {"extra": "ignore"}

    url: str
    label: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_excerpt: str | None = None


class SemanticAmbiguity(BaseModel):
    model_config = {"extra": "ignore"}

    field: str
    question: str
    options: list[str] = Field(default_factory=list)
    source_excerpt: str | None = None


class SemanticUnclassifiedEvidence(BaseModel):
    model_config = {"extra": "ignore"}

    evidence: str
    note: str | None = None
    source_excerpt: str | None = None


class SemanticResumeExtraction(BaseModel):
    model_config = {"extra": "ignore"}

    candidate_name: str | None = None
    professional_headline: SemanticHeadline | None = None
    professional_summary: SemanticSummary | None = None
    experiences: list[SemanticExperience] = Field(default_factory=list)
    education: list[SemanticEducation] = Field(default_factory=list)
    skills: list[SemanticSkill] = Field(default_factory=list)
    certifications: list[SemanticCertification] = Field(default_factory=list)
    languages: list[SemanticLanguage] = Field(default_factory=list)
    projects: list[SemanticProject] = Field(default_factory=list)
    publications: list[SemanticPublication] = Field(default_factory=list)
    links: list[SemanticLink] = Field(default_factory=list)
    ambiguities: list[SemanticAmbiguity] = Field(default_factory=list)
    unclassified_evidence: list[SemanticUnclassifiedEvidence] = Field(default_factory=list)

    @classmethod
    def get_json_schema(cls) -> dict[str, Any]:
        """Returns JSON schema tailored for OpenAI Structured Outputs."""
        return cls.model_json_schema()
