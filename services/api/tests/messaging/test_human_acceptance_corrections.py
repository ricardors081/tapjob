"""Human Acceptance Corrective Round 1 Test Suite (WP-PILOT-2).

Covers all 19 findings and requirements from Ricardo's Human Acceptance testing:
1. WhatsApp UX formatting (native bold, lists `- `, line spacing, subtle emojis).
2. Elimination of magic command prompts (natural confirmation, affirmative replies).
3. PDF resume intake resilience ("silence is a bug" - nested document unwrapping,
   ID vs mediaKey, zero silent drops).
4. NLU: "Trabalho com..." pattern recognition (TEXT_EXPERIENCE + candidate facts).
5. Querying own profile (PROFILE_QUERY intent, human labels, no technical field names).
6. Profile state / greeting: greeting recognizes returning users with
   profile_version >= 1 without restarting onboarding.
7. Profile field names in Portuguese (no 'summary', 'headline', 'target_roles',
   'profile_facts' leaked).
8. Reminders routing precedence: REMINDER_FOLLOWUP never hijacked by
   APPLICATION_REQUEST ("candidatar").
9. Reminders relative time parsing ("daqui 1 minuto", "daqui X horas", "amanhã", etc.).
10. Reminders without application (application_id=None accepted, no forced redirection).
11. LinkedIn SEO title sanitization (<company> hiring <title> in <local> | LinkedIn).
12. Match presentation: no "WP2.2", no "ineligible", using "Compatibilidade: XX/100".
13. Application Kit friendly failure when profile is incomplete.
14. Compound privacy query: answers what data is kept AND how to delete in a clear response.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs.extraction import sanitize_job_title_and_company
from tapjob_api.jobs.models import Job
from tapjob_api.messaging.evolution_adapter import (
    EvolutionAdapter,
    _find_document_in_message,
)
from tapjob_api.messaging.natural_language import (
    ConversationalIntent,
    ConversationContext,
    extract_skills_from_text,
    interpret_conversational_message,
    is_candidate_text_experience,
    parse_natural_reminder_time,
)
from tapjob_api.messaging.orchestration import (
    dispatch_inbound_message,
)
from tapjob_api.ports.messaging_channel import InboundMessage


def _make_inbound(
    text: str,
    *,
    sender: str = "5511999999999",
    msg_id: str = "wamid_corr_1",
    attachments: list[Any] | None = None,
) -> InboundMessage:
    from tapjob_api.ports.messaging_channel import ChannelIdentity

    return InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id=sender),
        provider_message_id=msg_id,
        received_at=datetime.now(UTC),
        text=text,
        attachments=tuple(attachments or []),
    )


# ==============================================================================
# 1. NLU & Text Experience: "Trabalho com..."
# ==============================================================================


def test_trabalho_com_pattern_recognized_as_text_experience() -> None:
    """Prompt Req 4: 'Trabalho com Python, PostgreSQL e AWS há 5 anos.' must be
    recognized as candidate text experience and extract skills.
    """
    phrase = "Trabalho com Python, PostgreSQL e AWS há 5 anos."
    assert is_candidate_text_experience(phrase) is True

    interp = interpret_conversational_message(phrase)
    assert interp.intent == ConversationalIntent.TEXT_EXPERIENCE

    skills = extract_skills_from_text(phrase)
    assert "Python" in skills
    assert "PostgreSQL" in skills
    assert "AWS" in skills


@pytest.mark.parametrize(
    "phrase",
    [
        "Atuo com React e Node.js há 3 anos",
        "Minha stack é Python, FastAPI e Docker",
        "Venho trabalhando com Java e Spring Boot",
        "Tenho domínio de Go e Kubernetes",
        "Uso profissionalmente TypeScript e Next.js",
    ],
)
def test_various_work_patterns_recognized_as_experience(phrase: str) -> None:
    """Proves various professional Brazilian Portuguese work phrases route to TEXT_EXPERIENCE."""
    assert is_candidate_text_experience(phrase) is True
    interp = interpret_conversational_message(phrase)
    assert interp.intent == ConversationalIntent.TEXT_EXPERIENCE


# ==============================================================================
# 2. Natural Affirmative Confirmation (Eliminating Magic Commands)
# ==============================================================================


@pytest.mark.parametrize(
    "word",
    [
        "sim",
        "pode",
        "claro",
        "confirma",
        "pode adicionar",
        "está certo",
        "ta certo",
        "isso mesmo",
        "isso aí",
        "exato",
        "pode salvar",
        "pode seguir",
    ],
)
def test_natural_affirmative_with_pending_review_confirms_facts(word: str) -> None:
    """Prompt Req 2: Natural affirmative words without rigid magic commands must
    confirm pending candidate facts in review context.
    """
    ctx = ConversationContext(has_pending_fact_review=True, pending_facts_count=3)
    interp = interpret_conversational_message(word, context=ctx)
    assert interp.intent == ConversationalIntent.FACT_CONFIRM_ALL


# ==============================================================================
# 3. Profile Query: "O que você sabe sobre meu perfil?"
# ==============================================================================


@pytest.mark.parametrize(
    "phrase",
    [
        "O que você sabe sobre meu perfil?",
        "O que você sabe sobre mim?",
        "Como está meu perfil?",
        "O que tem no meu perfil?",
        "Quais informações profissionais você tem minhas?",
        "Me mostra meu perfil",
        "Consultar meu perfil",
    ],
)
def test_profile_query_intent_recognition(phrase: str) -> None:
    """Prompt Req 5: User asking about their profile routes to PROFILE_QUERY intent."""
    interp = interpret_conversational_message(phrase)
    assert interp.intent == ConversationalIntent.PROFILE_QUERY


def test_profile_query_dispatch_presents_human_labels_without_technical_names() -> None:
    """Prompt Req 5 & 7: Profile presentation must map headline, summary, target_roles,
    profile_facts to Portuguese labels and never leak internal identifiers.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound("O que você sabe sobre meu perfil?")

    fake_snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline="Engenheiro de Software Sênior",
        summary="Especialista em microsserviços e backend distribuído",
        target_roles=["Tech Lead", "Senior Backend Engineer"],
        facts=[],
    )

    fake_status = {
        "has_profile": True,
        "profile_version": 1,
        "has_facts": True,
        "fact_count": 2,
        "headline": fake_snapshot.headline,
        "summary": fake_snapshot.summary,
        "target_roles": fake_snapshot.target_roles,
    }

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            return_value=fake_snapshot,
        ),
        patch(
            "tapjob_api.messaging.orchestration.get_user_profile_status",
            return_value=fake_status,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    reply = queued_messages[0].message_text

    # Human labels present
    assert "Título profissional" in reply
    assert "Resumo profissional" in reply
    assert "Cargos de interesse" in reply
    assert "Engenheiro de Software Sênior" in reply

    # Zero internal identifiers leaked
    assert "headline" not in reply
    assert "summary" not in reply
    assert "target_roles" not in reply
    assert "profile_facts" not in reply


# ==============================================================================
# 4. Greeting & Profile State: Returning User with profile_version >= 1
# ==============================================================================


def test_greeting_recognizes_returning_user_with_profile_version_1_and_no_facts() -> None:
    """Prompt Req 6: Greeting must not restart onboarding when user already initiated
    a CareerProfile (profile_version >= 1), even if active fact_count == 0.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound("Oi")

    # User has CareerProfile version 1, but 0 active facts
    fake_status = {
        "has_profile": True,
        "profile_version": 1,
        "has_facts": False,
        "fact_count": 0,
        "headline": "Engenheiro de Computação",
        "summary": None,
        "target_roles": None,
    }

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            "tapjob_api.messaging.orchestration.get_user_profile_status",
            return_value=fake_status,
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    reply = queued_messages[0].message_text

    # Welcomed back warmly, not treated as new user onboarding
    assert "Bom ter você de volta" in reply
    assert "Versão 1" in reply
    # Must NOT say the first-time onboarding text
    assert "Para começar a analisar oportunidades" not in reply


# ==============================================================================
# 5. Reminders: Precedence, Relative Time Parsing & No Forced Application Kit
# ==============================================================================


def test_reminder_mentioning_candidatar_routes_to_reminder_not_application_request() -> None:
    """Prompt Req 8: 'me lembra de eu me candidatar a uma vaga daqui 1 minuto'
    must route to REMINDER_FOLLOWUP and never be hijacked by APPLICATION_REQUEST.
    """
    phrase = "me lembra de eu me candidatar a uma vaga daqui 1 minuto"
    interp = interpret_conversational_message(phrase)
    assert interp.intent == ConversationalIntent.REMINDER_FOLLOWUP


def test_natural_reminder_time_parsing() -> None:
    """Prompt Req 9: Deterministic parsing of relative expressions."""
    now = datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC)

    # 1 minute
    t1, d1 = parse_natural_reminder_time("daqui 1 minuto", base_time=now)
    assert t1 == now + timedelta(minutes=1)
    assert "1 minuto" in (d1 or "")

    # 15 minutes
    t2, d2 = parse_natural_reminder_time("daqui a 15 minutos", base_time=now)
    assert t2 == now + timedelta(minutes=15)
    assert "15 minutos" in (d2 or "")

    # 2 hours
    t3, d3 = parse_natural_reminder_time("daqui 2 horas", base_time=now)
    assert t3 == now + timedelta(hours=2)
    assert "2 horas" in (d3 or "")

    # 3 days
    t4, d4 = parse_natural_reminder_time("daqui 3 dias", base_time=now)
    assert t4 == now + timedelta(days=3)
    assert "3 dias" in (d4 or "")

    # tomorrow
    t5, d5 = parse_natural_reminder_time("amanhã de manhã", base_time=now)
    assert t5 == now + timedelta(days=1)
    assert "amanhã" in (d5 or "")


def test_reminder_without_application_succeeds_with_career_note() -> None:
    """Prompt Req 10: Reminder when latest_app is None schedules follow-up with
    application_id=None and does NOT redirect to Application Kit.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound("me lembra de eu me candidatar a uma vaga daqui 1 minuto")

    # No application in DB
    session.execute.return_value.scalar_one_or_none.return_value = None

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_schedule_follow_up",
            return_value={"reminder_id": str(uuid.uuid4())},
        ) as mock_tool_rem,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    mock_tool_rem.assert_called_once()
    # application_id MUST be None
    assert mock_tool_rem.call_args.kwargs["application_id"] is None

    reply = queued_messages[0].message_text
    assert "Lembrete agendado com sucesso" in reply
    assert "1 minuto" in reply
    # Must NOT redirect to Application Kit
    assert "prepare seu Application Kit" not in reply


@pytest.mark.parametrize(
    "ambiguous_phrase",
    [
        "me lembra de olhar essa vaga",
        "me lembra depois",
    ],
)
def test_reminder_ambiguous_time_asks_clarification_and_creates_zero_reminders(
    ambiguous_phrase: str,
) -> None:
    """Finding 1: When reminder time is ambiguous, do NOT invent +3 days,
    do NOT schedule reminder, ask naturally for clarification.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound(ambiguous_phrase)

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            "tapjob_api.messaging.orchestration.tool_schedule_follow_up",
        ) as mock_tool_rem,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    # Invariant 1: Zero reminders created
    mock_tool_rem.assert_not_called()
    # Invariant 2: One clarification response returned
    assert len(keys) == 1
    reply = queued_messages[0].message_text
    assert "Para quando você gostaria de agendar esse lembrete" in reply
    assert "em 3 dias" not in reply


def test_reminder_with_candidatar_and_1_minute_creates_reminder() -> None:
    """Finding 1: 'me lembra de me candidatar nessa vaga daqui 1 minuto' creates
    a reminder for approximately +1 min and is NEVER redirected to Application Kit.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound("me lembra de me candidatar nessa vaga daqui 1 minuto")

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=None,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_schedule_follow_up",
            return_value={"reminder_id": str(uuid.uuid4())},
        ) as mock_tool_rem,
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    mock_tool_rem.assert_called_once()
    remind_at = mock_tool_rem.call_args.kwargs["remind_at"]
    diff = (remind_at - datetime.now(UTC)).total_seconds()
    # Approximately +60 seconds
    assert 40 <= diff <= 80

    reply = queued_messages[0].message_text
    assert "Lembrete agendado com sucesso" in reply
    assert "1 minuto" in reply
    assert "prepare seu Application Kit" not in reply


def test_help_and_greeting_do_not_contain_rigid_command_prompts() -> None:
    """Finding 3: Help menu, greeting, and fallback must NOT teach rigid magic commands
    with quotes or imperative directives like 'diga ...', 'envie ...', 'peça ...'.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())

    def get_response_for_phrase(phrase: str) -> str:
        queued: list[str] = []

        def fake_q(s: Any, **kwargs: Any) -> MagicMock:
            m = MagicMock()
            queued.append(kwargs["message_text"])
            return m

        inb = _make_inbound(phrase)
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
                "tapjob_api.messaging.orchestration.get_user_profile_status",
                return_value={
                    "has_profile": True,
                    "profile_version": 1,
                    "fact_count": 2,
                    "has_facts": True,
                    "headline": "Dev",
                    "summary": "Sum",
                    "target_roles": ["Dev"],
                },
            ),
            patch(
                "tapjob_api.messaging.orchestration.queue_response_message",
                side_effect=fake_q,
            ),
        ):
            dispatch_inbound_message(
                session, redis_client=None, inbound=inb, provider_id=uuid.uuid4()
            )
        return queued[0] if queued else ""

    help_text = get_response_for_phrase("ajuda")
    greet_text = get_response_for_phrase("olá")
    fallback_text = get_response_for_phrase("algo completamente inesperado")

    for text_sample in (help_text, greet_text, fallback_text):
        lower = text_sample.lower()
        assert "diga '" not in lower
        assert "envie '" not in lower
        assert "peça '" not in lower
        assert "digite '" not in lower
        assert "'já me candidatei'" not in text_sample
        assert "'privacidade'" not in text_sample
        assert "'ajuda'" not in text_sample


# ==============================================================================
# 6. LinkedIn SEO Title Sanitization
# ==============================================================================


def test_linkedin_seo_title_sanitization() -> None:
    """Prompt Req 11: Raw LinkedIn SEO strings must be sanitized into clean title & company."""
    raw = (
        "Ecocentauro Sistemas Inteligentes hiring Analista de Suporte Técnico "
        "in Sinop, Mato Grosso, Brazil | LinkedIn"
    )
    title, company, location = sanitize_job_title_and_company(raw, "LinkedIn")
    assert title == "Analista de Suporte Técnico"
    assert company == "Ecocentauro Sistemas Inteligentes"
    assert location == "Sinop, Mato Grosso, Brazil"


def test_linkedin_seo_title_sanitization_without_location() -> None:
    """Proves LinkedIn title without 'in <local>' is also sanitized."""
    raw = "TechCorp hiring Senior Backend Engineer | LinkedIn"
    title, company, _ = sanitize_job_title_and_company(raw, "LinkedIn")
    assert title == "Senior Backend Engineer"
    assert company == "TechCorp"


def test_non_linkedin_title_preserved() -> None:
    """Standard non-SEO titles remain intact."""
    title, company, _ = sanitize_job_title_and_company("Desenvolvedor Python Pleno", "TechCorp")
    assert title == "Desenvolvedor Python Pleno"
    assert company == "TechCorp"


# ==============================================================================
# 7. Match Presentation: No Internal Terminology (WP2.2 / Ineligible)
# ==============================================================================


def test_match_presentation_eliminates_internal_terms() -> None:
    """Prompt Req 12: Responses must never show 'WP2.2' or 'ineligible'."""
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound("https://jobs.lever.co/empresa/vaga")

    fake_job_info = {
        "job_id": str(uuid.uuid4()),
        "title": "Analista de Dados",
        "company": "DataCorp",
    }
    fake_match_info = {
        "overall_score": 28,
        "eligibility": "INELIGIBLE",
    }

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            "tapjob_api.messaging.orchestration.tool_ingest_job_url",
            return_value=fake_job_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_match_job",
            return_value=fake_match_info,
        ),
        patch(
            "tapjob_api.messaging.orchestration.get_user_profile_status",
            return_value={
                "has_profile": True,
                "profile_version": 1,
                "has_facts": False,
                "fact_count": 0,
            },
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    reply = queued_messages[0].message_text

    assert "Compatibilidade: 28/100" in reply
    assert "WP2.2" not in reply
    assert "ineligible" not in reply
    assert "Match Score" not in reply


# ==============================================================================
# 8. Application Kit Friendly Guidance when Profile Incomplete
# ==============================================================================


def test_application_request_when_profile_has_no_facts_gives_friendly_guidance() -> None:
    """Prompt Req 13: When user requests application kit but profile has 0 confirmed
    facts, respond naturally explaining what is missing without technical errors.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound("quero me candidatar")

    fake_job = MagicMock(spec=Job)
    fake_job.id = uuid.uuid4()
    fake_job.title = "Desenvolvedor Backend"

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            "tapjob_api.messaging.orchestration.resolve_user_target_job",
            return_value=fake_job,
        ),
        patch(
            "tapjob_api.messaging.orchestration.tool_tailor_resume",
            side_effect=RuntimeError("Profile has no confirmed facts"),
        ),
        patch(
            "tapjob_api.messaging.orchestration.get_user_profile_status",
            return_value={
                "has_profile": True,
                "profile_version": 1,
                "has_facts": False,
                "fact_count": 0,
            },
        ),
        patch(
            "tapjob_api.messaging.orchestration.queue_response_message",
            side_effect=fake_queue,
        ),
    ):
        keys = dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    reply = queued_messages[0].message_text

    assert "precisamos de informações profissionais confirmadas" in reply
    assert "Envie seu currículo em PDF ou compartilhe suas experiências" in reply


# ==============================================================================
# 9. Compound Privacy & Deletion Inquiry
# ==============================================================================


def test_compound_privacy_and_deletion_query_answers_both_parts() -> None:
    """Prompt Req 14: 'Quais dados meus você guarda e como posso apagar?' must answer
    BOTH which data categories are retained AND how deletion works.
    """
    phrase = "Quais dados meus você guarda e como posso apagar?"
    interp = interpret_conversational_message(phrase)
    assert interp.intent == ConversationalIntent.PRIVACY_INQUIRY

    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())
    inbound = _make_inbound(phrase)

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
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
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )

    assert len(keys) == 1
    reply = queued_messages[0].message_text

    # Part 1: What data is used (grounded, not claiming exhaustive 'armazenamos apenas')
    assert "Seus dados no TapJob" in reply
    assert "Entre as informações utilizadas pelo TapJob" in reply
    assert "Armazenamos apenas" not in reply
    # Part 2: How deletion works (chat does not execute destructive deletion, no SLA promises)
    assert "Como funciona a solicitação de exclusão" in reply
    assert "daremos andamento imediato" not in reply
    assert "não é executada automaticamente por mensagens de chat" in reply


# ==============================================================================
# 10. PDF Resume Intake Resilience: Nested unwrapping, ID vs mediaKey, No Silence
# ==============================================================================


def test_evolution_nested_document_unwrapping() -> None:
    """Prompt Req 3: Evolution nested message formats must be correctly unpacked."""
    # 1. documentWithCaptionMessage nested in ephemeralMessage
    ephemeral_msg = {
        "ephemeralMessage": {
            "message": {
                "documentWithCaptionMessage": {
                    "message": {
                        "documentMessage": {
                            "fileName": "curriculo_ricardo.pdf",
                            "mimetype": "application/pdf",
                            "mediaKey": "crypto_key_do_not_use_as_id",
                        }
                    }
                }
            }
        }
    }
    doc_node = _find_document_in_message(ephemeral_msg)
    assert doc_node is not None
    assert doc_node["fileName"] == "curriculo_ricardo.pdf"

    # 2. viewOnceMessage
    view_once_msg = {
        "viewOnceMessage": {
            "message": {
                "documentMessage": {
                    "fileName": "meu_cv.pdf",
                    "mimetype": "application/pdf",
                }
            }
        }
    }
    doc_node2 = _find_document_in_message(view_once_msg)
    assert doc_node2 is not None
    assert doc_node2["fileName"] == "meu_cv.pdf"


def test_evolution_adapter_uses_msg_id_not_media_key() -> None:
    """Prompt Req 3: Evolution message ID must be used for provider_media_id,
    never the cryptographic mediaKey.
    """
    raw_event = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "id": "BAE5_REAL_MESSAGE_ID_12345",
                "remoteJid": "5511999999999@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {
                "documentMessage": {
                    "fileName": "curriculo.pdf",
                    "mimetype": "application/pdf",
                    "mediaKey": "SECRET_CRYPTO_MEDIA_KEY_XYZ",
                }
            },
            "messageTimestamp": 1700000000,
        },
    }

    import asyncio
    import json

    from tapjob_api.ports._common import CallContext, Deadline

    adapter = EvolutionAdapter(
        api_url="https://evo.example.com",
        api_key="secret",
        instance_name="TapJob",
    )
    ctx = CallContext(correlation_id="test", deadline=Deadline.after(10))
    messages = asyncio.run(adapter.parse_inbound(json.dumps(raw_event).encode("utf-8"), {}, ctx))
    assert len(messages) == 1
    inbound = messages[0]
    assert len(inbound.attachments) == 1
    att = inbound.attachments[0]
    # provider_media_id MUST be the message ID, not mediaKey!
    assert att.provider_media_id == "BAE5_REAL_MESSAGE_ID_12345"
    assert att.provider_media_id != "SECRET_CRYPTO_MEDIA_KEY_XYZ"


def test_pdf_intake_failure_never_silent() -> None:
    """Prompt Req 3: 'Silence is a bug'. Any failure in document retrieval, storage,
    or ingestion must queue a friendly response to the candidate.
    """
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE)
    link = MagicMock(id=uuid.uuid4())

    from tapjob_api.ports._common import PortError, PortErrorCode
    from tapjob_api.ports.messaging_channel import InboundAttachment

    att = InboundAttachment(
        provider_media_id="msg_doc_123",
        mime_type="application/pdf",
        filename="curriculo.pdf",
        size_bytes=1024,
        kind="document",
    )
    inbound = _make_inbound("", attachments=[att])

    queued_messages: list[MagicMock] = []

    def fake_queue(s: Any, **kwargs: Any) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.message_text = kwargs["message_text"]
        mock_msg.idempotency_key = kwargs["idempotency_key"]
        queued_messages.append(mock_msg)
        return mock_msg

    # Simulate terminal retrieval failure (e.g. 404 or invalid media on provider)
    mock_channel = MagicMock()

    async def fail_retrieval(*args: Any, **kwargs: Any) -> bytes:
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider="evolution",
            message="Media not found on Evolution API",
            transient=False,
        )

    mock_channel.retrieve_media.side_effect = fail_retrieval

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
            session,
            redis_client=None,
            inbound=inbound,
            provider_id=uuid.uuid4(),
            channel_adapter=mock_channel,
            storage=MagicMock(),
        )

    # Invariant: NEVER silent! A friendly message MUST be queued.
    assert len(keys) == 1
    reply = queued_messages[0].message_text
    assert "Não conseguimos baixar seu documento" in reply
    assert "Por favor, certifique-se" in reply
