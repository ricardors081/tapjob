"""Application bootstrap and dependency composition.

Owns the concrete provider selection and wiring for external services and AI gateways.
Keeps domain packages (career_profile, resume_import, messaging) provider-neutral.
"""

from __future__ import annotations

import os

from tapjob_api.ai_gateway.openai_gateway import OpenAiGateway
from tapjob_api.core.config import RuntimeSettings
from tapjob_api.ports.resume_parser import ResumeParser
from tapjob_api.resume_import.parsing import DeterministicResumeParser
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser


def create_resume_parser(
    settings: RuntimeSettings | None = None,
    *,
    provider: str | None = None,
    openai_api_key: str | None = None,
) -> ResumeParser:
    """Composes provider-neutral ResumeParser implementation from runtime configuration."""
    target_provider: str
    target_api_key: str | None
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"

    if settings is not None:
        target_provider = (
            (provider if provider is not None else (settings.ai_provider or "")).strip().lower()
        )
        target_api_key = openai_api_key if openai_api_key is not None else settings.openai_api_key
        model = settings.ai_resume_extraction_model
        base_url = settings.openai_base_url
    else:
        target_provider = (
            (provider if provider is not None else os.getenv("AI_PROVIDER", "openai"))
            .strip()
            .lower()
        )
        target_api_key = (
            openai_api_key if openai_api_key is not None else os.getenv("OPENAI_API_KEY")
        )
        model = os.getenv("AI_RESUME_EXTRACTION_MODEL", "gpt-4o-mini")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    if target_provider == "openai":
        if not target_api_key or not target_api_key.strip():
            raise ValueError("OpenAI API key is required when AI_PROVIDER=openai")
        ai_gateway = OpenAiGateway(
            api_key=target_api_key,
            model=model,
            base_url=base_url,
            endpoint="/responses",
        )
        return SemanticResumeParser(ai_gateway=ai_gateway)

    if target_provider == "deterministic":
        return DeterministicResumeParser()

    raise ValueError(f"Unsupported AI provider: {target_provider}")
