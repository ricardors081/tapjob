"""Comprehensive Natural Language Evaluation and Regression Test Suite for WP-PILOT-2.

Tests diverse Brazilian Portuguese phrasing variations:
- formal
- informal & colloquial
- short (curta)
- long (longa)
- typos & missing accents (erro de digitação)
- slang (gíria)
- indirect questions
- contextual continuity (affirmative replies)
- negative (rejection)
- ambiguous
- candidate text experience
"""

from __future__ import annotations

import pytest

from tapjob_api.messaging.natural_language import (
    ConversationalIntent,
    classify_conversational_intent,
    extract_single_action_details,
    extract_skills_from_text,
    normalize_text,
)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        # --- 1. APPLICATION REQUEST (J-10) ---
        # Canonical
        ("quero me candidatar", ConversationalIntent.APPLICATION_REQUEST),
        ("quero me candidatar nessa vaga", ConversationalIntent.APPLICATION_REQUEST),
        # Informal & Slang
        ("curti essa vaga, como faço?", ConversationalIntent.APPLICATION_REQUEST),
        (
            "curti demais essa vaga, como que faz pra entrar?",
            ConversationalIntent.APPLICATION_REQUEST,
        ),
        ("me ajuda a aplicar", ConversationalIntent.APPLICATION_REQUEST),
        ("ajuda a aplicar nessa vaga", ConversationalIntent.APPLICATION_REQUEST),
        ("quero tentar essa oportunidade", ConversationalIntent.APPLICATION_REQUEST),
        ("faz minha candidatura", ConversationalIntent.APPLICATION_REQUEST),
        ("bora aplicar nessa", ConversationalIntent.APPLICATION_REQUEST),
        ("vamos tentar essa", ConversationalIntent.APPLICATION_REQUEST),
        ("vamos aplicar nessa vaga", ConversationalIntent.APPLICATION_REQUEST),
        ("manda bala nessa vaga", ConversationalIntent.APPLICATION_REQUEST),
        ("como me candidato?", ConversationalIntent.APPLICATION_REQUEST),
        ("como me inscrevo nessa vaga?", ConversationalIntent.APPLICATION_REQUEST),
        ("gerar kit", ConversationalIntent.APPLICATION_REQUEST),
        ("monta meu kit de candidatura", ConversationalIntent.APPLICATION_REQUEST),
        (
            "gostei dessa vaga, como faco pra me candidatar?",
            ConversationalIntent.APPLICATION_REQUEST,
        ),
        ("pode aplicar pra mim", ConversationalIntent.APPLICATION_REQUEST),
        # Typo / missing accents
        ("quero me candidata", ConversationalIntent.APPLICATION_REQUEST),
        ("curti a vaga como faco", ConversationalIntent.APPLICATION_REQUEST),
        # --- 2. MATCH EXPLANATION (J-08, J-09) ---
        # Formal & Direct
        ("analisa essa vaga", ConversationalIntent.MATCH_EXPLANATION),
        ("analisar compatibilidade", ConversationalIntent.MATCH_EXPLANATION),
        ("explicar match", ConversationalIntent.MATCH_EXPLANATION),
        # Informal & Conversational questions (must NOT trigger recommendations!)
        ("o que você acha dessa vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("o que achou dessa vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("o que acha da vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("vale a pena me candidatar?", ConversationalIntent.MATCH_EXPLANATION),
        ("qual meu match nela?", ConversationalIntent.MATCH_EXPLANATION),
        ("qual o meu match nessa vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("tenho chance nessa vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("tenho chances?", ConversationalIntent.MATCH_EXPLANATION),
        ("sou compativel com essa vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("da pra explicar melhor essa vaga?", ConversationalIntent.MATCH_EXPLANATION),
        ("me da uma luz sobre essa vaga", ConversationalIntent.MATCH_EXPLANATION),
        ("sera que tenho chance nessa oportunidade?", ConversationalIntent.MATCH_EXPLANATION),
        # --- 3. FACT CONFIRMATION ALL (J-04) ---
        ("confirmar todos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("confirmar fatos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("pode confirmar", ConversationalIntent.FACT_CONFIRM_ALL),
        ("pode salvar", ConversationalIntent.FACT_CONFIRM_ALL),
        ("salvar informacoes", ConversationalIntent.FACT_CONFIRM_ALL),
        ("salvar dados", ConversationalIntent.FACT_CONFIRM_ALL),
        ("tá certo", ConversationalIntent.FACT_CONFIRM_ALL),
        ("tudo certo", ConversationalIntent.FACT_CONFIRM_ALL),
        ("tudo ok", ConversationalIntent.FACT_CONFIRM_ALL),
        ("perfeito", ConversationalIntent.FACT_CONFIRM_ALL),
        ("tá perfeito", ConversationalIntent.FACT_CONFIRM_ALL),
        ("aprovar todos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("aceito tudo", ConversationalIntent.FACT_CONFIRM_ALL),
        ("show de bola, salva aí", ConversationalIntent.FACT_CONFIRM_ALL),
        ("blz pode confirmar", ConversationalIntent.FACT_CONFIRM_ALL),
        ("pode cadastrar", ConversationalIntent.FACT_CONFIRM_ALL),
        # --- 4. FACT SINGLE ACTION (J-05) ---
        ("rejeita o 2", ConversationalIntent.FACT_SINGLE_ACTION),
        ("rejeita a 1", ConversationalIntent.FACT_SINGLE_ACTION),
        ("tira o fato 3 fora", ConversationalIntent.FACT_SINGLE_ACTION),
        ("descarta o item 2", ConversationalIntent.FACT_SINGLE_ACTION),
        ("ignorar fato 4", ConversationalIntent.FACT_SINGLE_ACTION),
        ("aceita o 1", ConversationalIntent.FACT_SINGLE_ACTION),
        ("confirma o item 2", ConversationalIntent.FACT_SINGLE_ACTION),
        ("editar fato 1: Dev Python Sênior", ConversationalIntent.FACT_SINGLE_ACTION),
        ("muda o 3 para Tech Lead", ConversationalIntent.FACT_SINGLE_ACTION),
        ("corrigir fato 2: FastAPI", ConversationalIntent.FACT_SINGLE_ACTION),
        # --- 5. FACT REJECT ALL ---
        ("rejeitar todos", ConversationalIntent.FACT_REJECT_ALL),
        ("descartar todos", ConversationalIntent.FACT_REJECT_ALL),
        ("ignorar todos", ConversationalIntent.FACT_REJECT_ALL),
        ("rejeita tudo", ConversationalIntent.FACT_REJECT_ALL),
        ("descarta tudo", ConversationalIntent.FACT_REJECT_ALL),
        ("joga fora tudo", ConversationalIntent.FACT_REJECT_ALL),
        ("nao quero nada disso", ConversationalIntent.FACT_REJECT_ALL),
        # --- 6. APPLICATION STATUS UPDATE (J-12) ---
        ("já me candidatei", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("ja me candidatei", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("me candidatei", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("acabei de aplicar", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("candidatura enviada", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("ja enviei o curriculo", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("mandei cv hoje cedo", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("ja mandei a aplicacao", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        ("marcar aplicada", ConversationalIntent.APPLICATION_STATUS_UPDATE),
        # --- 7. APPLICATION STATUS QUERY (J-13) ---
        ("minhas candidaturas", ConversationalIntent.APPLICATION_STATUS_QUERY),
        ("status da candidatura", ConversationalIntent.APPLICATION_STATUS_QUERY),
        ("como estão minhas candidaturas?", ConversationalIntent.APPLICATION_STATUS_QUERY),
        ("onde me candidatei?", ConversationalIntent.APPLICATION_STATUS_QUERY),
        # --- 8. REMINDERS / FOLLOW-UP (J-14) ---
        ("me lembra de acompanhar", ConversationalIntent.REMINDER_FOLLOWUP),
        ("me lembra", ConversationalIntent.REMINDER_FOLLOWUP),
        ("agendar lembrete", ConversationalIntent.REMINDER_FOLLOWUP),
        ("follow-up", ConversationalIntent.REMINDER_FOLLOWUP),
        ("avisa daqui 3 dias", ConversationalIntent.REMINDER_FOLLOWUP),
        ("me da um toque semana que vem", ConversationalIntent.REMINDER_FOLLOWUP),
        ("quando devo cobrar o recrutador?", ConversationalIntent.REMINDER_FOLLOWUP),
        # --- 9. PRIVACY & DELETION (J-15, J-16) ---
        ("privacidade", ConversationalIntent.PRIVACY_INQUIRY),
        ("lgpd", ConversationalIntent.PRIVACY_INQUIRY),
        ("politica de privacidade", ConversationalIntent.PRIVACY_INQUIRY),
        ("como tratam meus dados?", ConversationalIntent.PRIVACY_INQUIRY),
        ("excluir meus dados", ConversationalIntent.DELETION_REQUEST),
        ("deletar meus dados", ConversationalIntent.DELETION_REQUEST),
        ("apagar dados", ConversationalIntent.DELETION_REQUEST),
        ("apaga tudo que tem sobre mim", ConversationalIntent.DELETION_REQUEST),
        ("cancelar cadastro", ConversationalIntent.DELETION_REQUEST),
        ("confirmar exclusao", ConversationalIntent.DELETION_REQUEST),
        # --- 10. HELP & MENU (J-17) ---
        ("ajuda", ConversationalIntent.HELP_MENU),
        ("help", ConversationalIntent.HELP_MENU),
        ("socorro preciso de ajuda", ConversationalIntent.HELP_MENU),
        ("o que você faz?", ConversationalIntent.HELP_MENU),
        ("como funciona o tapjob?", ConversationalIntent.HELP_MENU),
        ("menu", ConversationalIntent.HELP_MENU),
        ("comandos disponiveis", ConversationalIntent.HELP_MENU),
        # --- 11. GREETING & RETURN (J-01, J-18) ---
        ("oi", ConversationalIntent.GREETING_OR_RETURN),
        ("olá", ConversationalIntent.GREETING_OR_RETURN),
        ("bom dia", ConversationalIntent.GREETING_OR_RETURN),
        ("boa tarde", ConversationalIntent.GREETING_OR_RETURN),
        ("boa noite", ConversationalIntent.GREETING_OR_RETURN),
        ("e aí meu consagrado", ConversationalIntent.GREETING_OR_RETURN),
        ("fala tu", ConversationalIntent.GREETING_OR_RETURN),
        ("voltei", ConversationalIntent.GREETING_OR_RETURN),
        # --- 12. RECOMMENDATIONS (General) ---
        ("quais vagas tem pra mim?", ConversationalIntent.JOB_RECOMMENDATIONS),
        ("vagas recomendadas", ConversationalIntent.JOB_RECOMMENDATIONS),
        ("recomendar vagas", ConversationalIntent.JOB_RECOMMENDATIONS),
        ("outras vagas", ConversationalIntent.JOB_RECOMMENDATIONS),
        ("tem mais vagas?", ConversationalIntent.JOB_RECOMMENDATIONS),
        ("vagas", ConversationalIntent.JOB_RECOMMENDATIONS),
        # --- 13. AFFIRMATIVE CONTEXTUAL (J-20) ---
        ("sim", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("sim por favor", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("quero", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("bora", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("com certeza", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("manda", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("claro", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        ("faz isso", ConversationalIntent.AFFIRMATIVE_CONTEXTUAL),
        # --- 14. TEXT EXPERIENCE (J-03) ---
        (
            "Tenho 6 anos de experiência como Desenvolvedor Python e trabalho com Docker e FastAPI",
            ConversationalIntent.TEXT_EXPERIENCE,
        ),
        (
            "Sou Engenheiro de Software pleno com foco em microsserviços, PostgreSQL e AWS",
            ConversationalIntent.TEXT_EXPERIENCE,
        ),
    ],
)
def test_natural_language_intent_classification(
    phrase: str, expected: ConversationalIntent
) -> None:
    intent = classify_conversational_intent(phrase)
    assert intent == expected, f"Failed for phrase: '{phrase}' (got {intent}, expected {expected})"


def test_single_action_extraction_details() -> None:
    cases = [
        ("rejeita o 2", ("reject", 2, None)),
        ("tira o fato 3", ("reject", 3, None)),
        ("descarta o item 4", ("reject", 4, None)),
        ("aceita a 1", ("accept", 1, None)),
        ("aprova o fato 5", ("accept", 5, None)),
        ("editar fato 1: Dev Python", ("edit", 1, "dev python")),
        ("muda o 4 para Tech Lead", ("edit", 4, "tech lead")),
        ("corrigir fato 2 = Senior Engineer", ("edit", 2, "senior engineer")),
    ]
    for phrase, expected in cases:
        norm = normalize_text(phrase)
        action, idx, val = extract_single_action_details(norm)
        assert (action, idx, val) == expected, f"Failed for action: '{phrase}'"


def test_extract_skills_from_text() -> None:
    sample = "Trabalho com Python, FastAPI, Docker, Celery e PostgreSQL na AWS."
    skills = extract_skills_from_text(sample)
    assert "Python" in skills
    assert "FastAPI" in skills
    assert "Docker" in skills
    assert "PostgreSQL" in skills
    assert "Celery" in skills
    assert "AWS" in skills
    assert "Java" not in skills
