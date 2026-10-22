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
    is_rich_embedded_job_description,
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


def test_candidate_text_experience_stages_ready_for_review_candidates(
    mock_session: MagicMock,
) -> None:
    """Turn 2: Candidate text experience stages ResumeImport in READY_FOR_REVIEW with candidates."""
    from tapjob_api.resume_import.models import ImportStatus, ResumeImport, ResumeImportCandidate

    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock()
    link.id = uuid.uuid4()
    link.user_id = user.id

    staged_items: list[Any] = []
    mock_session.add = MagicMock(side_effect=staged_items.append)
    mock_session.scalars.return_value.all.return_value = []

    text_msg = (
        "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
        "FastAPI, PostgreSQL e Docker na AWS."
    )
    inbound = _make_inbound(text_msg, msg_id="exp_turn_2")

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
    assert "exp_text_exp_turn_2" in keys[0]
    expected_msg = "Recebemos suas experiências profissionais com sucesso!"
    assert expected_msg in queued_messages[0].message_text
    assert "Competências identificadas:" in queued_messages[0].message_text

    # Verify that ResumeImport was created with READY_FOR_REVIEW
    imports = [item for item in staged_items if isinstance(item, ResumeImport)]
    assert len(imports) == 1
    assert imports[0].status == ImportStatus.READY_FOR_REVIEW
    assert imports[0].user_id == user.id

    # Verify that ResumeImportCandidates were created for skills
    candidates = [item for item in staged_items if isinstance(item, ResumeImportCandidate)]
    assert len(candidates) >= 5  # Python, FastAPI, PostgreSQL, Docker, AWS + headline
    skill_names = {c.value_json.get("name") for c in candidates if c.candidate_kind == "skill"}
    assert "Python" in skill_names
    assert "FastAPI" in skill_names
    assert "Docker" in skill_names


def test_job_url_intake_falls_back_to_text_when_url_fails(mock_session: MagicMock) -> None:
    """Turn 4: When job URL fetch fails with 404, falls back to ingesting text in the message."""
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock()
    link.id = uuid.uuid4()
    link.user_id = user.id

    job_text = (
        "Vaga: Desenvolvedor Python Pleno na TechCorp. "
        "Requisitos: Python, FastAPI, Docker, microsserviços. "
        "Link: https://jobs.lever.co/techcorp/python-pleno"
    )
    inbound = _make_inbound(job_text, msg_id="job_turn_4")

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

    fake_ingested = {
        "job_id": str(uuid.uuid4()),
        "title": "Desenvolvedor Python Pleno",
        "company": "TechCorp",
    }
    fake_match = {
        "overall_score": 88,
        "eligibility": "HIGH",
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
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            side_effect=RuntimeError("404 Client Error: Not Found"),
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_text",
            return_value=fake_ingested,
        ) as mock_ingest_text,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match,
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
    assert mock_ingest_text.called
    expected_vaga = "Vaga analisada: Desenvolvedor Python Pleno (TechCorp)."
    assert expected_vaga in queued_messages[0].message_text
    assert "Match Score WP2.2: 88/100 (HIGH)." in queued_messages[0].message_text


def test_privacy_intent_and_pasted_job_text_heuristics() -> None:
    """Verifies that privacy detection and pasted job text heuristics avoid false positives."""
    from tapjob_api.messaging.orchestration import is_pasted_job_text, is_privacy_intent

    # Turn 2 text should NOT be detected as a pasted job text
    turn_2_text = (
        "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
        "FastAPI, PostgreSQL e Docker na AWS."
    )
    assert not is_pasted_job_text(turn_2_text)

    # Real job text SHOULD be detected as a pasted job text
    real_job = (
        "Vaga: Desenvolvedor Python Pleno na TechCorp.\n"
        "Requisitos: 3 anos de experiência em Python, Docker, microsserviços.\n"
        "Benefícios: Plano de saúde, VR, modelo remoto."
    )
    assert is_pasted_job_text(real_job)

    # Turn 3 text should NOT be detected as a privacy inquiry
    turn_3_text = "tudo certo, pode salvar meus dados"
    assert not is_privacy_intent(turn_3_text)

    # Explicit privacy inquiry SHOULD be detected
    assert is_privacy_intent("privacidade")
    assert is_privacy_intent("política de privacidade")
    assert is_privacy_intent("excluir meus dados")
    assert is_privacy_intent("apagar dados")


def test_turn_2_to_turn_3_cascade_and_downstream_unblocked(mock_session: MagicMock) -> None:
    """Cascade Regression: Turn 2 (intake) -> Turn 3 ('tudo certo, pode salvar meus dados').

    Proves:
    1. Turn 2 stages ResumeImport in READY_FOR_REVIEW with candidate facts.
    2. Turn 3 phrase 'tudo certo, pode salvar meus dados' is NOT hijacked by privacy,
       routes to FACT_CONFIRM_ALL, finalizes the import, updates profile_version >= 1
       with active facts.
    3. Downstream application request (Turn 5) is unblocked because profile facts are confirmed.
    """
    from tapjob_api.resume_import.models import ImportStatus, ResumeImport, ResumeImportCandidate

    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4(), user_id=user.id)
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    # --- Turn 2: Text Experience Intake ---
    turn_2_inbound = _make_inbound(
        "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
        "FastAPI, PostgreSQL e Docker na AWS.",
        msg_id="turn_2_msg",
    )
    staged_items: list[Any] = []
    mock_session.add = MagicMock(side_effect=staged_items.append)
    mock_session.scalars.return_value.all.return_value = []

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
        keys_2 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=turn_2_inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_2) == 1
    assert (
        "Recebemos suas experiências profissionais com sucesso!" in queued_messages[0].message_text
    )
    staged_imports = [item for item in staged_items if isinstance(item, ResumeImport)]
    assert len(staged_imports) == 1
    resume_import = staged_imports[0]
    assert resume_import.status == ImportStatus.READY_FOR_REVIEW

    # Attach candidates to import for review
    candidates = [item for item in staged_items if isinstance(item, ResumeImportCandidate)]
    assert len(candidates) >= 5
    for c in candidates:
        if c.id is None:
            c.id = uuid.uuid4()
    resume_import.candidates = candidates

    # --- Turn 3: Fact Confirmation with exact live canary phrase ---
    turn_3_inbound = _make_inbound("tudo certo, pode salvar meus dados", msg_id="turn_3_msg")
    queued_messages.clear()

    # Mock DB query for latest READY_FOR_REVIEW import
    mock_session.execute.return_value.scalar_one_or_none.return_value = resume_import
    mock_session.scalars.return_value.all.return_value = []

    mock_finalize_res = MagicMock()
    mock_finalize_res.resume_version.profile_version = 1

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
            return_value=MagicMock(profile_version=0),
        ),
        patch(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            return_value=mock_finalize_res,
        ) as mock_finalize,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys_3 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=turn_3_inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_3) == 1
    mock_finalize.assert_called_once()
    assert "fact_conf_turn_3_msg" in keys_3[0]
    confirm_text = queued_messages[0].message_text
    assert "Informações confirmadas com sucesso!" in confirm_text
    assert "Versão 1" in confirm_text
    assert "Privacidade e Proteção de Dados" not in confirm_text

    # --- Downstream (Turn 5): Application Request is NOT blocked by unconfirmed facts ---
    turn_5_inbound = _make_inbound(
        "curti essa vaga, como faço pra me candidatar?", msg_id="turn_5_msg"
    )
    queued_messages.clear()

    job = Job(
        id=uuid.uuid4(),
        title="Desenvolvedor Python Pleno",
        status=JobStatus.ACTIVE,
    )
    fake_tailored = {
        "tailored_resume": "Desenvolvedor experiente em Python e FastAPI",
        "match_highlights": ["Python", "FastAPI"],
    }
    fake_kit = {
        "kit_id": str(uuid.uuid4()),
        "status": ApplicationStatus.PREPARING.value,
        "tailored_draft": fake_tailored,
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
        keys_5 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=turn_5_inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_5) >= 1
    app_text = queued_messages[0].message_text
    assert "Não foi possível gerar o Application Kit no momento" not in app_text
    assert "Application Kit preparado com sucesso" in app_text


def test_job_url_intake_fast_path_with_embedded_text_does_not_block_on_remote_url(
    mock_session: MagicMock,
) -> None:
    """Proves Turn 4 input (URL + rich description) uses fast path without calling
    tool_ingest_job_url.
    """
    inbound = _make_inbound(
        "Vaga: Desenvolvedor Python Pleno na TechCorp. Requisitos: Python, FastAPI, Docker. "
        "Link: https://jobs.lever.co/techcorp/python-pleno",
        msg_id="turn_4_fastpath",
    )
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    fake_job_info = {
        "job_id": str(uuid.uuid4()),
        "title": "Desenvolvedor Python Pleno",
        "company": "TechCorp",
    }
    fake_match_info = {
        "overall_score": 88,
        "eligibility": "ELIGIBLE",
    }
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
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_text",
            return_value=fake_job_info,
        ) as mock_ingest_text,
        patch("tapjob_api.messaging.orchestration.tool_ingest_job_url") as mock_ingest_url,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    # Invariant: tool_ingest_job_url was NEVER called!
    mock_ingest_url.assert_not_called()
    mock_ingest_text.assert_called_once()
    assert (
        mock_ingest_text.call_args.kwargs["source_url"]
        == "https://jobs.lever.co/techcorp/python-pleno"
    )
    assert "TechCorp" in mock_ingest_text.call_args.kwargs["company_hint"]
    assert len(keys) == 1
    assert "job_url_turn_4_fastpath" in keys[0]
    reply = queued_messages[0].message_text
    assert "Vaga analisada: Desenvolvedor Python Pleno (TechCorp)" in reply
    assert "Match Score WP2.2: 88/100 (ELIGIBLE)" in reply
    assert "quero me candidatar" in reply


def test_job_url_intake_url_only_timeout_fails_fast_with_paste_prompt(
    mock_session: MagicMock,
) -> None:
    """Proves URL-only message on timeout returns clear prompt to paste description
    without Celery retry.
    """
    from tapjob_api.jobs.errors import JobUrlUnreachableError

    inbound = _make_inbound(
        "https://jobs.lever.co/techcorp/python-pleno",
        msg_id="turn_4_timeout",
    )
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
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
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            side_effect=JobUrlUnreachableError("Connection timed out"),
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys) == 1
    reply = queued_messages[0].message_text
    assert "Não foi possível acessar a vaga pelo link fornecido" in reply
    assert "copie e cole aqui o texto com a descrição" in reply


def test_job_url_intake_url_only_success_calls_tool_ingest_job_url(
    mock_session: MagicMock,
) -> None:
    """Proves URL-only message when remote fetch succeeds calls tool_ingest_job_url canonically."""
    inbound = _make_inbound(
        "https://jobs.lever.co/techcorp/python-pleno",
        msg_id="turn_4_canonical",
    )
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    fake_job_info = {
        "job_id": str(uuid.uuid4()),
        "title": "Desenvolvedor Python Pleno",
        "company": "TechCorp",
    }
    fake_match_info = {
        "overall_score": 92,
        "eligibility": "HIGHLY_ELIGIBLE",
    }
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
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            return_value=fake_job_info,
        ) as mock_ingest_url,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    mock_ingest_url.assert_called_once_with(
        mock_session, user=user, url="https://jobs.lever.co/techcorp/python-pleno"
    )
    assert len(keys) == 1
    reply = queued_messages[0].message_text
    assert "Vaga analisada: Desenvolvedor Python Pleno (TechCorp)" in reply
    assert "Match Score WP2.2: 92/100 (HIGHLY_ELIGIBLE)" in reply


def test_is_rich_embedded_job_description_distinguishes_wrappers_from_real_jobs() -> None:
    """Verifies that conversational wrappers and questions return False while genuine
    structured job descriptions return True.
    """
    # 1. Conversational questions containing section keywords MUST return False
    # QUESTION_REQUISITOS_FALSE
    assert not is_rich_embedded_job_description(
        "Quais são os requisitos dessa vaga? Estou pensando em me candidatar e queria saber "
        "se combina comigo."
    )
    assert not is_rich_embedded_job_description(
        "Você consegue me dizer quais são os requisitos dessa vaga antes de eu me candidatar?"
    )

    # QUESTION_BENEFICIOS_REQUISITOS_FALSE
    assert not is_rich_embedded_job_description(
        "Quero entender os benefícios e requisitos dessa vaga antes de decidir "
        "se vou me candidatar."
    )

    # QUESTION_RESPONSABILIDADES_FALSE
    assert not is_rich_embedded_job_description(
        "Essa vaga parece interessante. Quais responsabilidades ela tem e será que combina "
        "com meu perfil?"
    )

    # Conversational wrappers accompanying a URL
    assert not is_rich_embedded_job_description("Achei essa vaga na Empresa X, pode analisar?")
    assert not is_rich_embedded_job_description("Achei essa vaga nessa empresa, consegue analisar?")
    assert not is_rich_embedded_job_description("vaga legal, olha isso:")
    assert not is_rich_embedded_job_description(
        "Olá assistente do TapJob, achei essa vaga excelente na Empresa XPTO e queria saber "
        "se dá match com o meu perfil cadastrado."
    )
    assert not is_rich_embedded_job_description(
        "Tenho 5 anos de experiência trabalhando com Python na AWS e queria saber desta vaga."
    )

    # 2. Genuine structured job postings MUST return True
    # TURN4_STRUCTURED_DESCRIPTION_TRUE
    assert is_rich_embedded_job_description(
        "Vaga: Desenvolvedor Python Pleno na TechCorp. "
        "Requisitos: Python, FastAPI, Docker, microsserviços."
    )

    # MULTILINE_STRUCTURED_POSTING_TRUE
    assert is_rich_embedded_job_description(
        "Desenvolvedor Backend Pleno\n"
        "Responsabilidades:\n"
        "- Desenvolver e manter microsserviços em Python e FastAPI\n"
        "- Projetar bancos de dados PostgreSQL\n"
        "Requisitos:\n"
        "- Experiência com Docker, Celery e Redis\n"
        "Benefícios:\n"
        "- Vale refeição, plano de saúde, trabalho remoto"
    )


def test_conversational_job_url_wrapper_calls_url_intake_not_text_fastpath(
    mock_session: MagicMock,
) -> None:
    """Conversational wrapper accompanying a URL must execute canonical tool_ingest_job_url
    and NOT bypass URL intake into text intake fastpath.
    """
    inbound = _make_inbound(
        "Achei essa vaga na Empresa X, pode analisar? https://jobs.lever.co/empresa-x/vaga",
        msg_id="conv_url_wrapper",
    )
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    fake_job_info = {
        "job_id": str(uuid.uuid4()),
        "title": "Engenheiro de Software",
        "company": "Empresa X",
    }
    fake_match_info = {
        "overall_score": 85,
        "eligibility": "ELIGIBLE",
    }
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
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            return_value=fake_job_info,
        ) as mock_ingest_url,
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_text",
        ) as mock_ingest_text,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    # Invariant: URL intake was called, text intake was NOT called
    mock_ingest_url.assert_called_once_with(
        mock_session, user=user, url="https://jobs.lever.co/empresa-x/vaga"
    )
    mock_ingest_text.assert_not_called()
    assert len(keys) == 1
    reply = queued_messages[0].message_text
    assert "Vaga analisada: Engenheiro de Software (Empresa X)" in reply
    assert "Match Score WP2.2: 85/100 (ELIGIBLE)" in reply


def test_conversational_question_with_job_url_calls_url_intake_not_text_fastpath(
    mock_session: MagicMock,
) -> None:
    """Conversational question mentioning requirements + job URL must execute canonical
    tool_ingest_job_url and NOT bypass URL intake into text intake fastpath.
    """
    inbound = _make_inbound(
        "Quais são os requisitos dessa vaga? Estou pensando em me candidatar e queria saber "
        "se combina comigo. https://jobs.lever.co/empresa-x/vaga",
        msg_id="conv_question_url",
    )
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    fake_job_info = {
        "job_id": str(uuid.uuid4()),
        "title": "Engenheiro de Software",
        "company": "Empresa X",
    }
    fake_match_info = {
        "overall_score": 85,
        "eligibility": "ELIGIBLE",
    }
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
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            return_value=fake_job_info,
        ) as mock_ingest_url,
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_text",
        ) as mock_ingest_text,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    # Invariant: URL intake was called, text intake was NOT called
    mock_ingest_url.assert_called_once_with(
        mock_session, user=user, url="https://jobs.lever.co/empresa-x/vaga"
    )
    mock_ingest_text.assert_not_called()
    assert len(keys) == 1
    reply = queued_messages[0].message_text
    assert "Vaga analisada: Engenheiro de Software (Empresa X)" in reply


def test_turn_4_rich_embedded_description_with_dead_url_calls_text_fastpath(
    mock_session: MagicMock,
) -> None:
    """Turn 4 rich embedded job description accompanied by an unreachable URL must execute
    fastpath tool_ingest_job_text without depending on remote URL fetch.
    """
    inbound = _make_inbound(
        "Vaga: Desenvolvedor Python Pleno na TechCorp. "
        "Requisitos: Python, FastAPI, Docker, microsserviços. "
        "Link: https://dead-domain.invalid/techcorp/python-pleno",
        msg_id="turn_4_dead_url_fastpath",
    )
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    fake_job_info = {
        "job_id": str(uuid.uuid4()),
        "title": "Desenvolvedor Python Pleno",
        "company": "TechCorp",
    }
    fake_match_info = {
        "overall_score": 88,
        "eligibility": "ELIGIBLE",
    }
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
            return_value=user,
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity",
            return_value=link,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_ingest_job_text",
            return_value=fake_job_info,
        ) as mock_ingest_text,
        patch("tapjob_api.messaging.orchestration.tool_ingest_job_url") as mock_ingest_url,
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound,
            provider_id=uuid.uuid4(),
        )

    # Invariant: tool_ingest_job_url was NEVER called!
    mock_ingest_url.assert_not_called()
    mock_ingest_text.assert_called_once()
    assert (
        mock_ingest_text.call_args.kwargs["source_url"]
        == "https://dead-domain.invalid/techcorp/python-pleno"
    )
    assert len(keys) == 1
    assert "job_url_turn_4_dead_url_fastpath" in keys[0]
    reply = queued_messages[0].message_text
    assert "Vaga analisada: Desenvolvedor Python Pleno (TechCorp)" in reply
    assert "Match Score WP2.2: 88/100 (ELIGIBLE)" in reply


def test_j05_single_fact_rejection_and_confirmation_cascade(mock_session: MagicMock) -> None:
    """J-05 Isolated Integration Test: Single Fact Rejection & Cascade Confirmation.

    Sequence:
    1. Register experience in text, staging 5 candidate facts (ResumeImport in READY_FOR_REVIEW).
    2. Reject candidate fact 2 individually ("rejeita o 2").
       Verify:
       - Fato 2 rejected response returned.
       - CandidateReviewDraft created with decision='reject' for candidate 2.
       - confirm_candidates_and_finalize is NOT called.
       - Profile version remains 0 (truth store untouched, zero premature alteration).
    3. Confirm remaining candidate facts ("confirmar todos").
       Verify:
       - confirm_candidates_and_finalize is called exactly once.
       - Candidate 2 decision remains REJECT.
       - Candidates 1, 3, 4, 5 decisions are ACCEPT.
       - Only 4 facts activated into career profile, rejected fact remains inactive.
       - Profile version transitions to 1.
    """
    from tapjob_api.resume_import.commands import ReviewDecisionKind
    from tapjob_api.resume_import.models import (
        CandidateReviewDraft,
        ImportStatus,
        ResumeImport,
        ResumeImportCandidate,
    )

    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4(), user_id=user.id)
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    # --- Step 1: Text Experience Intake (5 Candidate Facts) ---
    step_1_inbound = _make_inbound(
        "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
        "FastAPI, PostgreSQL, Docker e AWS.",
        msg_id="j05_step1_intake",
    )
    staged_items: list[Any] = []
    mock_session.add = MagicMock(side_effect=staged_items.append)
    mock_session.scalars.return_value.all.return_value = []

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
        keys_1 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=step_1_inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_1) == 1
    staged_imports = [item for item in staged_items if isinstance(item, ResumeImport)]
    assert len(staged_imports) == 1
    resume_import = staged_imports[0]
    assert resume_import.status == ImportStatus.READY_FOR_REVIEW

    candidates = [item for item in staged_items if isinstance(item, ResumeImportCandidate)]
    assert len(candidates) >= 5
    # Ensure exactly 5 candidate facts as specified in J-05 requirement
    candidates = candidates[:5]
    for c in candidates:
        if c.id is None:
            c.id = uuid.uuid4()
    resume_import.candidates = candidates
    ordered_cands = sorted(candidates, key=lambda c: (c.created_at, str(c.id)))
    assert len(ordered_cands) == 5

    # --- Step 2: Reject Fact 2 Individually ("rejeita o 2") ---
    step_2_inbound = _make_inbound("rejeita o 2", msg_id="j05_step2_reject_2")
    queued_messages.clear()

    active_drafts: list[CandidateReviewDraft] = []
    mock_session.add = MagicMock(side_effect=active_drafts.append)
    mock_session.execute.return_value.scalar_one_or_none.return_value = resume_import
    mock_session.scalars.return_value.all.side_effect = lambda: list(active_drafts)

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
        ) as mock_finalize_premature,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys_2 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=step_2_inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_2) == 1
    # Verify zero premature finalization / profile alteration
    mock_finalize_premature.assert_not_called()

    # Verify candidate 2 draft was created with REJECT
    assert len(active_drafts) == 1
    draft_c2 = active_drafts[0]
    assert draft_c2.candidate_id == ordered_cands[1].id
    assert draft_c2.decision == ReviewDecisionKind.REJECT.value

    # Verify user received rejection feedback with remaining pending count
    reply_step_2 = queued_messages[0].message_text
    assert "Fato 2 rejeitado com sucesso." in reply_step_2
    assert "Restam 4 fato(s) pendente(s)" in reply_step_2

    # --- Step 3: Confirm Remaining Facts ("confirmar todos") ---
    step_3_inbound = _make_inbound("confirmar todos", msg_id="j05_step3_confirm_all")
    queued_messages.clear()

    mock_finalize_res = MagicMock()
    mock_finalize_res.resume_version.profile_version = 1

    captured_decisions: list[Any] = []

    def fake_finalize(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal captured_decisions
        decisions_arg = kwargs.get("decisions")
        if decisions_arg:
            captured_decisions = list(decisions_arg)
        return mock_finalize_res

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
            return_value=MagicMock(profile_version=0),
        ),
        patch(
            "tapjob_api.messaging.orchestration.confirm_candidates_and_finalize",
            side_effect=fake_finalize,
        ) as mock_finalize,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys_3 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=step_3_inbound,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_3) == 1
    mock_finalize.assert_called_once()
    assert "fact_conf_j05_step3_confirm_all" in keys_3[0]

    # Verify decision coverage: exactly 5 decisions
    assert len(captured_decisions) == len(ordered_cands)
    dec_by_cand = {d.candidate_id: d for d in captured_decisions}

    # Candidate 2 MUST be REJECT
    assert dec_by_cand[ordered_cands[1].id].decision == ReviewDecisionKind.REJECT

    # Candidates 1, 3, 4, 5 MUST be ACCEPT
    accepted_cands = [
        c
        for i, c in enumerate(ordered_cands)
        if i != 1 and dec_by_cand[c.id].decision == ReviewDecisionKind.ACCEPT
    ]
    assert len(accepted_cands) == 4

    # Verify final profile version updated to 1
    reply_step_3 = queued_messages[0].message_text
    assert "Informações confirmadas com sucesso!" in reply_step_3
    assert "Versão 1" in reply_step_3


def test_j09_active_job_match_inquiry_grounded_in_target_job(mock_session: MagicMock) -> None:
    """J-09 Isolated Test: Natural Job Inquiries Scoped & Grounded in Target Job.

    Validates:
    1. Conversational inquiry ("o que você acha dessa vaga?") with active target job
       produces a response grounded strictly in the active job's requirements and candidate profile.
    2. Response is scoped strictly to the target job (no data leakage).
    3. When no active target job is present, fails closed with a clear guidance reply
       and zero hallucination.
    """
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4(), user_id=user.id)
    target_job = Job(
        id=uuid.uuid4(),
        title="Desenvolvedor Python Sênior",
        status=JobStatus.ACTIVE,
    )
    queued_messages: list[MagicMock] = []

    def fake_queue(session: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    fake_match_info = {
        "overall_score": 85,
        "eligibility": "HIGH_MATCH",
    }
    fake_expl_info = {
        "strengths": [{"skill": "Python"}, {"skill": "FastAPI"}],
        "missing_requirements": [{"skill": "Kubernetes"}],
    }

    # Case 1: Inbound question with active target job
    inbound_with_job = _make_inbound("o que você acha dessa vaga?", msg_id="j09_query_active")

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
            return_value=target_job,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_explain_match",
            return_value=fake_expl_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound_with_job,
            provider_id=uuid.uuid4(),
        )

    assert len(keys) == 1
    assert "match_expl_j09_query_active" in keys[0]
    reply = queued_messages[0].message_text
    assert "Desenvolvedor Python Sênior" in reply
    assert "85/100" in reply
    assert "Python" in reply
    assert "FastAPI" in reply
    assert "Kubernetes" in reply

    # Case 2: Inbound question when no active target job exists
    queued_messages.clear()
    inbound_no_job = _make_inbound("o que você acha dessa vaga?", msg_id="j09_query_no_job")

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
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys_no_job = dispatch_inbound_message(
            mock_session,
            redis_client=MagicMock(),
            inbound=inbound_no_job,
            provider_id=uuid.uuid4(),
        )

    assert len(keys_no_job) == 1
    reply_no_job = queued_messages[0].message_text
    assert "Nenhuma vaga encontrada para análise" in reply_no_job
    assert "Envie o link ou texto da vaga" in reply_no_job


def test_j22_inbound_message_replay_idempotency_prevents_duplicate_domain_effects(
    mock_session: MagicMock,
) -> None:
    """J-22 Isolated Integration Test: Replay & Idempotency Safety across Inbound Processing.

    Proves:
    1. Processing the exact same inbound message twice through dispatch_inbound_message
       results in:
       - No duplicate Applications.
       - No duplicate ApplicationKits.
       - No duplicate Reminders.
       - No duplicate OutboundMessageQueue entries.
       - Domain state completely unchanged on second execution.
       - Idempotency keys returned are identical between execution 1 and 2.
    """
    from tapjob_api.messaging.applications import (
        Application,
        ApplicationKit,
        ApplicationStatus,
        ApplyMode,
    )
    from tapjob_api.messaging.outbox import OutboundMessageQueue
    from tapjob_api.messaging.reminders import Reminder, ReminderStatus

    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4(), user_id=user.id)
    job = Job(id=uuid.uuid4(), title="Engenheiro de Software", status=JobStatus.ACTIVE)

    # --- Scenario A: Application & Kit Preparation Replay ---
    inbound_app = _make_inbound("quero me candidatar", msg_id="j22_app_replay_msg")

    domain_applications: list[Application] = []
    domain_kits: list[ApplicationKit] = []
    domain_outbox: dict[str, OutboundMessageQueue] = {}

    fake_tailored = {
        "tailored_resume": "Engenheiro de Software com experiência comprovada",
        "match_highlights": ["Python", "FastAPI"],
    }
    fake_kit_id = uuid.uuid4()
    fake_app_id = uuid.uuid4()

    def fake_build_kit(
        session: Any, redis_client: Any, *, user: Any, job_id: Any
    ) -> dict[str, Any]:
        if not domain_kits:
            app = Application(
                id=fake_app_id,
                user_id=user.id,
                job_id=job_id,
                status=ApplicationStatus.PREPARING,
                apply_mode=ApplyMode.MANUAL,
            )
            domain_applications.append(app)
            kit = ApplicationKit(
                id=fake_kit_id,
                application_id=app.id,
                user_id=user.id,
                job_id=job_id,
                profile_version=1,
            )
            domain_kits.append(kit)
        return {
            "kit_id": str(fake_kit_id),
            "application_id": str(fake_app_id),
            "status": ApplicationStatus.PREPARING.value,
            "tailored_draft": fake_tailored,
        }

    def fake_queue_outbox(
        session: Session,
        *,
        channel_identity_link_id: Any,
        recipient_external_id: str,
        message_text: str,
        idempotency_key: str,
    ) -> OutboundMessageQueue:
        if idempotency_key in domain_outbox:
            return domain_outbox[idempotency_key]
        msg = OutboundMessageQueue(
            id=uuid.uuid4(),
            channel_identity_link_id=str(channel_identity_link_id),
            message_content={
                "text": message_text,
                "recipient_external_id": recipient_external_id,
            },
            idempotency_key=idempotency_key,
        )
        domain_outbox[idempotency_key] = msg
        return msg

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
            side_effect=fake_build_kit,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue_outbox,
        ),
    ):
        # Run 1
        keys_run1 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound_app,
            provider_id=uuid.uuid4(),
        )
        assert len(domain_applications) == 1
        assert len(domain_kits) == 1
        outbox_count_run1 = len(domain_outbox)

        # Run 2 (Replay with same message ID and text)
        keys_run2 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound_app,
            provider_id=uuid.uuid4(),
        )

        # Assert zero domain duplication on replay
        assert len(domain_applications) == 1, "Application was duplicated on replay!"
        assert len(domain_kits) == 1, "ApplicationKit was duplicated on replay!"
        assert len(domain_outbox) == outbox_count_run1, "Outbound message was duplicated on replay!"
        assert keys_run1 == keys_run2, "Idempotency keys differed across replays!"

    # --- Scenario B: Reminder Scheduling Replay ---
    inbound_rem = _make_inbound("me lembra de acompanhar", msg_id="j22_rem_replay_msg")
    domain_reminders: dict[str, Reminder] = {}

    def fake_schedule_followup(
        session: Any,
        *,
        user: Any,
        remind_at: Any,
        application_id: Any,
        note: Any,
        channel_identity_link_id: Any,
    ) -> dict[str, Any]:
        key = f"rem_{user.id}_follow_up"
        if key not in domain_reminders:
            rem = Reminder(
                id=uuid.uuid4(),
                user_id=user.id,
                channel_identity_link_id=channel_identity_link_id,
                remind_at=remind_at,
                intent="application_follow_up",
                status=ReminderStatus.PENDING,
                idempotency_key=key,
            )
            domain_reminders[key] = rem
        existing_rem = domain_reminders[key]
        return {
            "reminder_id": str(existing_rem.id),
            "intent": existing_rem.intent,
            "remind_at": existing_rem.remind_at.isoformat(),
            "status": existing_rem.status.value,
        }

    mock_session.execute.return_value.scalar_one_or_none.return_value = domain_applications[0]

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
            "tapjob_api.messaging.orchestration.tool_schedule_follow_up",
            side_effect=fake_schedule_followup,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue_outbox,
        ),
    ):
        # Run 1
        rem_keys_run1 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound_rem,
            provider_id=uuid.uuid4(),
        )
        assert len(domain_reminders) == 1
        outbox_count_rem1 = len(domain_outbox)

        # Run 2 (Replay with same message ID)
        rem_keys_run2 = dispatch_inbound_message(
            mock_session,
            redis_client=None,
            inbound=inbound_rem,
            provider_id=uuid.uuid4(),
        )

        # Assert zero reminder duplication on replay
        assert len(domain_reminders) == 1, "Reminder was duplicated on replay!"
        assert len(domain_outbox) == outbox_count_rem1, (
            "Outbound message was duplicated on reminder replay!"
        )
        assert rem_keys_run1 == rem_keys_run2, "Reminder idempotency keys differed!"
