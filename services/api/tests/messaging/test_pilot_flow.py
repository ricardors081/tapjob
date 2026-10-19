"""Tests for WhatsApp pilot flow improvements: kit delivery and LGPD disclosures (WP-PILOT-1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs.models import Job, JobStatus
from tapjob_api.messaging.applications import ApplicationStatus
from tapjob_api.messaging.orchestration import (
    dispatch_inbound_message,
    format_tailored_application_kit_messages,
)
from tapjob_api.ports.messaging_channel import ChannelIdentity, InboundMessage


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock(spec=Session)
    session.add = MagicMock()
    session.flush = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    return session


def _make_inbound(
    text: str, sender_id: str = "5511988887777", msg_id: str = "msg_1"
) -> InboundMessage:
    return InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id=sender_id),
        provider_message_id=msg_id,
        received_at=datetime.now(UTC),
        text=text,
    )


def test_first_contact_greeting_includes_lgpd_disclosure(mock_session: MagicMock) -> None:
    """First contact greeting to an unknown phone must include clear LGPD privacy disclosure."""
    inbound = _make_inbound("Olá")
    provider_id = uuid.uuid4()
    queued_messages: list[MagicMock] = []

    def fake_queue(
        session: Session,
        *,
        channel_identity_link_id: Any,
        recipient_external_id: str,
        message_text: str,
        idempotency_key: str,
    ) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = message_text
        mock_msg.idempotency_key = idempotency_key
        queued_messages.append(mock_msg)
        return mock_msg

    with (
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.link_or_create_identity",
            return_value=MagicMock(id=uuid.uuid4()),
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound,
            provider_id=provider_id,
        )

    assert len(keys) == 1
    assert len(queued_messages) == 1
    text = queued_messages[0].message_text
    assert "LGPD" in text
    assert "privacidade" in text.lower()
    assert "assistente de carreira" in text.lower()


def test_privacidade_command_returns_lgpd_information(mock_session: MagicMock) -> None:
    """Typing 'privacidade' returns legal bases, purposes, retention and deletion rights."""
    inbound = _make_inbound("privacidade")
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    with (
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys) == 1
    text = queued_messages[0].message_text
    assert "Privacidade e Proteção de Dados (LGPD)" in text
    assert "Bases Legais" in text
    assert "Art. 7º, V" in text
    assert "Art. 7º, IX" in text
    assert "excluir meus dados" in text


def test_excluir_meus_dados_command_returns_deletion_instructions(
    mock_session: MagicMock,
) -> None:
    """Typing 'excluir meus dados' returns explicit instructions for account/data deletion."""
    inbound = _make_inbound("quero excluir meus dados")
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    with (
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys) == 1
    text = queued_messages[0].message_text
    assert "Exclusão de Dados Pessoais (LGPD)" in text
    assert "eliminação dos dados" in text
    assert "confirmar exclusao" in text


def test_format_tailored_application_kit_messages_single_text() -> None:
    """When tailored kit text is concise, produces a single message containing all sections."""
    kit_info = {
        "kit_id": "kit_abc123",
        "tailored_draft": {
            "sections": {
                "summary": (
                    "Engenheiro de Software com experiência em Python, FastAPI e PostgreSQL."
                ),
                "skills": "Python, Docker, FastAPI, SQL",
                "experience_highlights": (
                    "Tech Lead na Empresa X; Desenvolvedor Senior na Empresa Y"
                ),
            }
        },
    }
    messages = format_tailored_application_kit_messages(
        job_title="Senior Backend Engineer",
        kit_info=kit_info,
        status_desc="Candidatura em preparação (Status: preparing)",
        cta_text="Envie 'já me candidatei' assim que submeter.",
        max_single_message_chars=1500,
    )

    assert len(messages) == 1
    content = messages[0]
    assert "Senior Backend Engineer" in content
    assert "ID do Kit: kit_abc123" in content
    assert "Resumo Profissional:" in content
    assert "Engenheiro de Software com experiência" in content
    assert "Experiências em Destaque:" in content
    assert "• Tech Lead na Empresa X" in content
    assert "Competências-Chave:" in content
    assert "• Python" in content
    assert "Envie 'já me candidatei'" in content


def test_format_tailored_application_kit_messages_splits_when_long() -> None:
    """When tailored kit text exceeds length threshold, splits deterministically into two parts."""
    long_summary = "Profissional sênior especializado em arquiteturas distribuídas. " * 30
    long_exp = (
        "Arquiteto de Software responsável por microsserviços em larga escala na Corp Z; " * 20
    )
    kit_info = {
        "kit_id": "kit_long_789",
        "tailored_draft": {
            "sections": {
                "summary": long_summary,
                "skills": "Go, Python, Kubernetes, AWS, Kafka",
                "experience_highlights": long_exp,
            }
        },
    }
    messages = format_tailored_application_kit_messages(
        job_title="Lead Architect",
        kit_info=kit_info,
        status_desc="Candidatura em preparação",
        cta_text="Envie 'já me candidatei' após submissão.",
        max_single_message_chars=500,  # Force split threshold
    )

    assert len(messages) == 2
    part1, part2 = messages[0], messages[1]
    assert "ID do Kit: kit_long_789" in part1
    assert "Resumo Profissional:" in part1
    assert "Experiências em Destaque (Lead Architect):" in part2
    assert "Competências-Chave:" in part2
    assert "já me candidatei" in part2


def test_dispatch_inbound_delivers_tailored_kit_in_text(mock_session: MagicMock) -> None:
    """When user says 'quero me candidatar', formatted kit text is queued in conversation."""
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    job = Job(id=uuid.uuid4(), title="Engenheiro Python Pleno", status=JobStatus.ACTIVE)
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    fake_tailored = {
        "sections": {
            "summary": "Desenvolvedor focado em Python e automações.",
            "skills": "Python, Celery, Redis",
            "experience_highlights": "Analista Pleno na Startup Alfa",
        }
    }
    fake_kit = {
        "kit_id": "kit_real_555",
        "status": ApplicationStatus.PREPARING.value,
        "tailored_draft": fake_tailored,
    }

    inbound = _make_inbound("quero me candidatar", msg_id="inbound_apply_99")

    with (
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=job,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_tailor_resume",
            return_value=fake_tailored,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_build_application_kit",
            return_value=fake_kit,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys) == 1
    assert keys[0] == "kit_inbound_apply_99"
    sent_text = queued_messages[0].message_text
    assert "Engenheiro Python Pleno" in sent_text
    assert "kit_real_555" in sent_text
    assert "Resumo Profissional:" in sent_text
    assert "Desenvolvedor focado em Python" in sent_text
    assert "Experiências em Destaque:" in sent_text
    assert "• Analista Pleno na Startup Alfa" in sent_text
    assert "Competências-Chave:" in sent_text
    assert "• Python" in sent_text
