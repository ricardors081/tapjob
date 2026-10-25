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


def create_resume_parser(settings: RuntimeSettings | None = None) -> ResumeParser:
    """Composes provider-neutral ResumeParser implementation from runtime configuration."""
    current_settings = settings or RuntimeSettings()
    if current_settings.openai_api_key and current_settings.openai_api_key.strip():
        ai_gateway = OpenAiGateway(
            api_key=current_settings.openai_api_key,
            model=current_settings.ai_resume_extraction_model,
            base_url=current_settings.openai_base_url,
        )
        return SemanticResumeParser(ai_gateway=ai_gateway)
    return DeterministicResumeParser()
