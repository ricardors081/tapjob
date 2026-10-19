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
    """First contact greeting to an unknown phone must be concise and product-focused."""
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
    assert "assistente de carreira" in text.lower()
    assert "privacidade" in text.lower()
    assert "dados" in text.lower()
    # Invariant: first contact does NOT dump legal articles or statutory disclaimers
    assert "art. 7" not in text.lower()
    assert "artigo" not in text.lower()


def test_privacidade_command_returns_lgpd_information(mock_session: MagicMock) -> None:
    """Typing 'privacidade' explains categories, purposes, retention, and rights."""
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
    assert "Privacidade e Proteção de Dados" in text
    assert "Dados Coletados" in text
    assert "Finalidade" in text
    assert "Retenção" in text
    assert "Seus Direitos" in text
    assert "excluir meus dados" in text
    # Invariant: no dogmatic statutory claims dumped in user chat
    assert "Art. 7º, V" not in text
    assert "Art. 7º, IX" not in text


def test_excluir_meus_dados_command_returns_deletion_instructions(
    mock_session: MagicMock,
) -> None:
    """Typing 'excluir meus dados' returns truthful operator-assisted deletion guidance."""
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
    assert "Solicitação de Exclusão de Dados" in text
    assert "operador do piloto" in text
    assert "Nenhum dado é excluído automaticamente sem essa validação prévia" in text
    # Invariant (Finding 1): no false execution promise "confirmar exclusao"
    assert "confirmar exclusao" not in text.lower()


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
    max_chars = 500
    messages = format_tailored_application_kit_messages(
        job_title="Lead Architect",
        kit_info=kit_info,
        status_desc="Candidatura em preparação",
        cta_text="Envie 'já me candidatei' após submissão.",
        max_single_message_chars=max_chars,
    )

    assert len(messages) > 1
    # Finding 2 invariant: strictly 0 < len(part) <= max_single_message_chars for EVERY part
    for idx, part in enumerate(messages):
        assert 0 < len(part) <= max_chars, f"Part {idx} length {len(part)} exceeds {max_chars}"

    all_content = "\n".join(messages)
    assert "ID do Kit: kit_long_789" in all_content
    assert "Resumo Profissional:" in all_content
    assert "Experiências em Destaque:" in all_content
    assert "Competências-Chave:" in all_content
    assert "já me candidatei" in all_content


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


def test_confirmar_exclusao_does_not_confirm_resume_facts(mock_session: MagicMock) -> None:
    """Proves 'confirmar exclusao' routes to deletion guidance and NEVER confirms resume facts."""
    inbound = _make_inbound("confirmar exclusao", msg_id="del_msg_1")
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
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
        ) as mock_finalize,
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
    # Verify finalize was NEVER called
    mock_finalize.assert_not_called()
    text = queued_messages[0].message_text
    # Finding 1 invariant: routes to deletion guidance and not resume confirmation
    assert "Solicitação de Exclusão de Dados" in text
    assert "perfil profissional foi atualizado" not in text


def test_confirmar_todos_confirms_pending_resume_facts(mock_session: MagicMock) -> None:
    """Proves 'confirmar todos' correctly triggers fact confirmation without deletion collision."""
    inbound = _make_inbound("confirmar todos", msg_id="confirm_all_1")
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    queued_messages: list[MagicMock] = []

    mock_import = MagicMock()
    mock_import.id = uuid.uuid4()
    mock_candidate = MagicMock()
    mock_candidate.id = uuid.uuid4()
    mock_candidate.created_at = datetime.now(UTC)
    mock_import.candidates = [mock_candidate]

    mock_session.execute.return_value.scalar_one_or_none.return_value = mock_import
    mock_session.scalars.return_value.all.return_value = []

    mock_res = MagicMock()
    mock_res.resume_version.profile_version = 2

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
            "tapjob_api.messaging.orchestration.get_truth_snapshot",
            return_value=MagicMock(profile_version=1),
        ),
        patch(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            return_value=mock_res,
        ) as mock_finalize,
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
    mock_finalize.assert_called_once()
    text = queued_messages[0].message_text
    assert "Informações confirmadas com sucesso" in text
    assert "Versão 2" in text


def test_job_description_with_politica_de_beneficios_routes_to_job_intake(
    mock_session: MagicMock,
) -> None:
    """Proves job text containing 'política de benefícios' is NOT captured by privacy routing."""
    job_text = (
        "Vaga: Engenheiro de Software Sênior\n"
        "Buscamos profissional com sólida experiência em microsserviços.\n"
        "A empresa oferece remuneração compatível com o mercado, excelente política de benefícios "
        "e política de trabalho remoto híbrido com flexibilidade total."
    )
    inbound = _make_inbound(job_text, msg_id="job_intake_1")
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    fake_parsed_job = {
        "job_id": str(uuid.uuid4()),
        "title": "Engenheiro de Software Sênior",
        "company": "Empresa XPTO",
        "description": job_text,
    }

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
            "tapjob_api.messaging.orchestration.tool_ingest_job_text",
            return_value=fake_parsed_job,
        ) as mock_ingest,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value={"overall_score": 90, "eligibility": "HIGH"},
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
    mock_ingest.assert_called_once()
    text = queued_messages[0].message_text
    # Finding 6 invariant: must NOT route to privacy handler
    assert "Privacidade e Proteção de Dados" not in text
    assert "Solicitação de Exclusão de Dados" not in text
    # Routed to job intake
    assert "Descrição da vaga registrada com sucesso" in text


def test_format_tailored_application_kit_extremely_long_summary_and_bullet() -> None:
    """Deterministic segmentation bounds single huge paragraphs and oversized bullets."""
    huge_summary = "X" * 3200
    huge_bullet = "Empresa Y: " + ("Desenvolvimento de algoritmos de alta performance. " * 50)
    kit_info = {
        "kit_id": "kit_huge_999",
        "tailored_draft": {
            "sections": {
                "summary": huge_summary,
                "skills": "Python, Rust, C++",
                "experience_highlights": huge_bullet,
            }
        },
    }
    max_chars = 400
    messages = format_tailored_application_kit_messages(
        job_title="Principal Scientist",
        kit_info=kit_info,
        status_desc="Status: preparing",
        cta_text="Pronto para envio.",
        max_single_message_chars=max_chars,
    )

    assert len(messages) >= 8
    # Finding 2 invariant: strictly 0 < len(part) <= max_single_message_chars for EVERY part
    for idx, part in enumerate(messages):
        assert 0 < len(part) <= max_chars, f"Part {idx} length {len(part)} exceeds {max_chars}"


def test_format_tailored_application_kit_ungrounded_fallback_when_empty() -> None:
    """When draft sections are empty, output avoids fabricated claims of adaptation/grounding."""
    kit_info = {
        "kit_id": "kit_empty_000",
        "tailored_draft": {
            "sections": {
                "summary": "",
                "skills": "",
                "experience_highlights": "",
            }
        },
    }
    messages = format_tailored_application_kit_messages(
        job_title="Junior Developer",
        kit_info=kit_info,
        status_desc="Status: pending",
        cta_text="Aguardando dados.",
        max_single_message_chars=1500,
    )

    assert len(messages) == 1
    content = messages[0]
    # Truthful fallback: no false claim of verified grounding when no profile content adapted
    assert "Nenhum conteúdo de perfil adaptado para esta vaga" in content
    assert "Nenhum resumo específico selecionado" in content
    assert "Nenhuma experiência específica selecionada" in content
    assert "Nenhuma competência específica selecionada" in content
    assert "Currículo adaptado com grounding 100% verificado" not in content


def test_dispatch_inbound_kit_delivery_idempotency_and_multipart_keys(
    mock_session: MagicMock,
) -> None:
    """Proves multipart kit delivery produces deterministic idempotency keys and bounds length."""
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    job = Job(id=uuid.uuid4(), title="Staff Engineer", status=JobStatus.ACTIVE)
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    long_summary = "Engenheiro sênior com vasta experiência em sistemas de larga escala. " * 25
    fake_tailored = {
        "sections": {
            "summary": long_summary,
            "skills": "Python, Distributed Systems, Go",
            "experience_highlights": "Tech Lead na Corp Alpha; Principal na Corp Beta",
        }
    }
    fake_kit = {
        "kit_id": "kit_multipart_123",
        "status": ApplicationStatus.PREPARING.value,
        "tailored_draft": fake_tailored,
    }

    inbound = _make_inbound("quero me candidatar", msg_id="apply_multipart_77")

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

    assert len(keys) == len(queued_messages)
    assert len(keys) >= 2
    # Deterministic keys format: kit_<provider_message_id>_part<N>
    for idx, key in enumerate(keys):
        assert key == f"kit_apply_multipart_77_part{idx + 1}"
        assert queued_messages[idx].idempotency_key == key
        assert len(queued_messages[idx].message_text) <= 1500
