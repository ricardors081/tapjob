"""Application bootstrap and dependency composition.

Owns the concrete provider selection and wiring for external services and AI gateways.
Keeps domain packages (career_profile, resume_import, messaging) provider-neutral.
"""

from __future__ import annotations

from tapjob_api.ai_gateway.openai_gateway import OpenAiGateway
from tapjob_api.core.config import RuntimeSettings
from tapjob_api.ports.resume_parser import ResumeParser
from tapjob_api.resume_import.parsing import DeterministicResumeParser
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser


def create_resume_parser(settings: RuntimeSettings) -> ResumeParser:
    """Composes provider-neutral ResumeParser implementation from runtime configuration."""
    target_provider = (settings.ai_provider or "").strip().lower()

    if target_provider == "openai":
        target_api_key = settings.openai_api_key
        if not target_api_key or not target_api_key.strip():
            raise ValueError("OpenAI API key is required when AI_PROVIDER=openai")
        ai_gateway = OpenAiGateway(
            api_key=target_api_key,
            model=settings.ai_resume_extraction_model,
            base_url=settings.openai_base_url,
            endpoint="/responses",
        )
        return SemanticResumeParser(ai_gateway=ai_gateway)

    if target_provider == "deterministic":
        return DeterministicResumeParser()

    raise ValueError(f"Unsupported AI provider: {target_provider}")
