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
    ConversationContext,
    IntentConfidence,
    classify_conversational_intent,
    classify_url_intent,
    extract_single_action_details,
    extract_skills_from_text,
    extract_urls,
    interpret_conversational_message,
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
        # --- 3. FACT CONFIRMATION ALL (J-04: Unambiguous commands) ---
        ("confirmar todos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("confirmar fatos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("confirmar meus dados", ConversationalIntent.FACT_CONFIRM_ALL),
        ("confirmar informacoes", ConversationalIntent.FACT_CONFIRM_ALL),
        ("aprovar todos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("aceito tudo", ConversationalIntent.FACT_CONFIRM_ALL),
        ("aceitar todos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("aceitar tudo", ConversationalIntent.FACT_CONFIRM_ALL),
        ("meus dados estao certos", ConversationalIntent.FACT_CONFIRM_ALL),
        ("meus dados estao corretos", ConversationalIntent.FACT_CONFIRM_ALL),
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
        ("onde vocês salvam meus dados?", ConversationalIntent.PRIVACY_INQUIRY),
        ("como vocês usam meus dados?", ConversationalIntent.PRIVACY_INQUIRY),
        ("quais dados vocês guardam?", ConversationalIntent.PRIVACY_INQUIRY),
        ("meus dados estão seguros?", ConversationalIntent.PRIVACY_INQUIRY),
        ("por que vocês precisam dos meus dados?", ConversationalIntent.PRIVACY_INQUIRY),
        ("por quanto tempo guardam meus dados?", ConversationalIntent.PRIVACY_INQUIRY),
        ("excluir meus dados", ConversationalIntent.DELETION_REQUEST),
        ("quero excluir meus dados", ConversationalIntent.DELETION_REQUEST),
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


def test_contextual_interpretation_with_job_and_fact_review() -> None:
    """Verifies that casual/affirmative phrases resolve accurately using ConversationContext."""
    # 1. 'essa parece boa' with active target job -> APPLICATION_REQUEST
    ctx_with_job = ConversationContext(
        has_target_job=True,
        target_job_title="Dev Python Pleno",
        target_job_company="TechCorp",
    )
    res_with_job = interpret_conversational_message("essa parece boa", context=ctx_with_job)
    assert res_with_job.intent == ConversationalIntent.APPLICATION_REQUEST
    assert res_with_job.confidence == IntentConfidence.HIGH
    assert res_with_job.extracted_entities.get("target_job_title") == "Dev Python Pleno"

    # 2. 'essa parece boa' WITHOUT active target job -> CLARIFICATION_NEEDED
    ctx_no_job = ConversationContext(has_target_job=False)
    res_no_job = interpret_conversational_message("essa parece boa", context=ctx_no_job)
    assert res_no_job.intent == ConversationalIntent.CLARIFICATION_NEEDED
    assert res_no_job.confidence == IntentConfidence.AMBIGUOUS
    assert res_no_job.clarification_question is not None
    assert "link ou o texto da vaga" in res_no_job.clarification_question

    # 3. 'bora' with pending fact review -> FACT_CONFIRM_ALL
    ctx_facts = ConversationContext(
        has_pending_fact_review=True,
        pending_facts_count=3,
    )
    res_facts = interpret_conversational_message("bora", context=ctx_facts)
    assert res_facts.intent == ConversationalIntent.FACT_CONFIRM_ALL
    assert res_facts.confidence == IntentConfidence.HIGH

    # 4. 'bora' standalone without context -> AFFIRMATIVE_CONTEXTUAL
    res_standalone = interpret_conversational_message("bora", context=None)
    assert res_standalone.intent == ConversationalIntent.AFFIRMATIVE_CONTEXTUAL
    assert res_standalone.confidence == IntentConfidence.MEDIUM

    # 5. 'sim' with kit prompt in outbox -> APPLICATION_REQUEST
    ctx_prompt_kit = ConversationContext(
        last_assistant_prompt="Deseja gerar o Application Kit para esta oportunidade?"
    )
    res_prompt = interpret_conversational_message("sim", context=ctx_prompt_kit)
    assert res_prompt.intent == ConversationalIntent.APPLICATION_REQUEST
    assert res_prompt.confidence == IntentConfidence.HIGH


def test_url_classification_and_safeguards() -> None:
    """Verifies that URLs are categorized accurately and generic URLs trigger safe clarification."""
    # 1. ATS URLs -> JOB_URL_INTAKE
    ats_urls = [
        "https://empresa.gupy.io/job/12345",
        "https://jobs.lever.co/empresa/abc-def",
        "https://boards.greenhouse.io/empresa/jobs/999",
        "https://www.linkedin.com/jobs/view/123456789/",
        "https://empresa.com/careers/vagas/python-senior",
    ]
    for url in ats_urls:
        interp = classify_url_intent(url)
        assert interp.intent == ConversationalIntent.JOB_URL_INTAKE, f"Failed for ATS URL: {url}"
        assert interp.extracted_entities.get("job_url") == url

    # 2. Portfolio / Profile URLs -> TEXT_EXPERIENCE
    profile_urls = [
        "https://github.com/candidato-dev",
        "https://gitlab.com/candidato",
        "https://www.linkedin.com/in/candidato-tech/",
        "https://behance.net/designer",
    ]
    for url in profile_urls:
        interp = classify_url_intent(url)
        assert interp.intent == ConversationalIntent.TEXT_EXPERIENCE, (
            f"Failed for profile URL: {url}"
        )
        assert interp.extracted_entities.get("portfolio_url") == url

    # 3. Privacy Policy URLs -> PRIVACY_INQUIRY
    priv_urls = [
        "https://empresa.com/politica-de-privacidade",
        "https://empresa.com/lgpd",
        "https://empresa.com/privacy",
    ]
    for url in priv_urls:
        interp = classify_url_intent(url)
        assert interp.intent == ConversationalIntent.PRIVACY_INQUIRY, (
            f"Failed for privacy URL: {url}"
        )

    # 4. Unknown / Generic URLs -> CLARIFICATION_NEEDED
    unknown_urls = [
        "https://medium.com/@autor/meu-artigo-tecnico",
        "https://noticias.globo.com/economia/reportagem.html",
        "https://exemplo.com/pagina-aleatoria",
    ]
    for url in unknown_urls:
        interp = classify_url_intent(url)
        assert interp.intent == ConversationalIntent.CLARIFICATION_NEEDED, (
            f"Failed for generic URL: {url}"
        )
        assert interp.confidence == IntentConfidence.AMBIGUOUS
        assert interp.clarification_question is not None
        assert "vaga de emprego" in interp.clarification_question


def test_extract_urls_cleans_punctuation() -> None:
    text = "Olha essa vaga: https://empresa.gupy.io/job/12345. E veja https://github.com/dev!"
    extracted = extract_urls(text)
    assert extracted == [
        "https://empresa.gupy.io/job/12345",
        "https://github.com/dev",
    ]


def test_fact_confirm_all_vs_privacy_inquiry_with_context() -> None:
    """Regression test verifying precedence between confirmation and privacy inquiries."""
    ctx_pending = ConversationContext(has_pending_fact_review=True, pending_facts_count=5)

    # A) "tudo certo, pode salvar meus dados" with pending review => FACT_CONFIRM_ALL
    res_a = interpret_conversational_message(
        "tudo certo, pode salvar meus dados", context=ctx_pending
    )
    assert res_a.intent == ConversationalIntent.FACT_CONFIRM_ALL
    assert res_a.confidence == IntentConfidence.HIGH

    # B) "pode salvar tudo" with pending review => FACT_CONFIRM_ALL
    res_b = interpret_conversational_message("pode salvar tudo", context=ctx_pending)
    assert res_b.intent == ConversationalIntent.FACT_CONFIRM_ALL
    assert res_b.confidence == IntentConfidence.HIGH

    # C) Contextual confirmations with pending review => FACT_CONFIRM_ALL
    for phrase in (
        "pode salvar",
        "salvar meus dados",
        "pode salvar meus dados",
        "tudo certo",
        "perfeito",
        "ta certo",
        "tudo ok",
    ):
        res_phrase = interpret_conversational_message(phrase, context=ctx_pending)
        assert res_phrase.intent == ConversationalIntent.FACT_CONFIRM_ALL

    # D) "onde vocês salvam meus dados?" => PRIVACY_INQUIRY (even with pending review!)
    res_d = interpret_conversational_message("onde vocês salvam meus dados?", context=ctx_pending)
    assert res_d.intent == ConversationalIntent.PRIVACY_INQUIRY
    assert res_d.confidence == IntentConfidence.HIGH

    # E) "como tratam meus dados?" => PRIVACY_INQUIRY
    res_e = interpret_conversational_message("como tratam meus dados?", context=ctx_pending)
    assert res_e.intent == ConversationalIntent.PRIVACY_INQUIRY
    assert res_e.confidence == IntentConfidence.HIGH

    # F) "quero excluir meus dados" => DELETION_REQUEST (highest priority LGPD action)
    res_f = interpret_conversational_message("quero excluir meus dados", context=ctx_pending)
    assert res_f.intent == ConversationalIntent.DELETION_REQUEST
    assert res_f.confidence == IntentConfidence.HIGH

    # G) WITHOUT pending review: ambiguous phrases must NOT be FACT_CONFIRM_ALL
    ctx_no_pending = ConversationContext(has_pending_fact_review=False)
    for ctx in (None, ctx_no_pending):
        # "você pode salvar meus dados?" => PRIVACY_INQUIRY (never FACT_CONFIRM_ALL)
        res_q = interpret_conversational_message("você pode salvar meus dados?", context=ctx)
        assert res_q.intent != ConversationalIntent.FACT_CONFIRM_ALL
        assert res_q.intent == ConversationalIntent.PRIVACY_INQUIRY

        # "tudo certo, pode salvar meus dados" without review => NOT FACT_CONFIRM_ALL
        res_no_ctx = interpret_conversational_message(
            "tudo certo, pode salvar meus dados", context=ctx
        )
        assert res_no_ctx.intent != ConversationalIntent.FACT_CONFIRM_ALL

        # "pode salvar tudo" without review => NOT FACT_CONFIRM_ALL
        res_pst = interpret_conversational_message("pode salvar tudo", context=ctx)
        assert res_pst.intent != ConversationalIntent.FACT_CONFIRM_ALL

        # "pode salvar meus dados" without review => NOT FACT_CONFIRM_ALL
        res_psmd = interpret_conversational_message("pode salvar meus dados", context=ctx)
        assert res_psmd.intent != ConversationalIntent.FACT_CONFIRM_ALL

    # Verify TEXT_EXPERIENCE behavior remains intact
    exp_text = (
        "Tenho 5 anos de experiência como Desenvolvedor Python e trabalho com "
        "FastAPI, PostgreSQL e Docker na AWS."
    )
    res_exp = interpret_conversational_message(exp_text, context=None)
    assert res_exp.intent == ConversationalIntent.TEXT_EXPERIENCE
    assert res_exp.confidence == IntentConfidence.HIGH
