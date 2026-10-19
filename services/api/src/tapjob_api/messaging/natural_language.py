"""Natural Language Understanding and Conversational Intent Engine for TapJob Messaging.

Designed for natural, colloquial, formal, and informal Brazilian Portuguese (PT-BR) interactions.
Replaces rigid, fragile keyword/substring checks with robust semantic classification,
eliminating routing collisions (e.g. asking about a job triggering recommendations).
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

COMMON_TECH_SKILLS: tuple[str, ...] = (
    "Python",
    "FastAPI",
    "Django",
    "Flask",
    "Docker",
    "Kubernetes",
    "AWS",
    "GCP",
    "Azure",
    "PostgreSQL",
    "MySQL",
    "Redis",
    "MongoDB",
    "React",
    "TypeScript",
    "JavaScript",
    "Node.js",
    "Go",
    "Golang",
    "Java",
    "Spring",
    "C#",
    ".NET",
    "Git",
    "Linux",
    "Celery",
    "GraphQL",
    "REST",
    "Terraform",
    "Kafka",
    "RabbitMQ",
    "CI/CD",
)


class ConversationalIntent(StrEnum):
    """Categorized conversational intents for WhatsApp pilot interactions."""

    DELETION_REQUEST = "DELETION_REQUEST"
    PRIVACY_INQUIRY = "PRIVACY_INQUIRY"
    FACT_CONFIRM_ALL = "FACT_CONFIRM_ALL"
    FACT_REJECT_ALL = "FACT_REJECT_ALL"
    FACT_SINGLE_ACTION = "FACT_SINGLE_ACTION"
    APPLICATION_STATUS_UPDATE = "APPLICATION_STATUS_UPDATE"
    APPLICATION_STATUS_QUERY = "APPLICATION_STATUS_QUERY"
    MATCH_EXPLANATION = "MATCH_EXPLANATION"
    APPLICATION_REQUEST = "APPLICATION_REQUEST"
    REMINDER_FOLLOWUP = "REMINDER_FOLLOWUP"
    JOB_RECOMMENDATIONS = "JOB_RECOMMENDATIONS"
    HELP_MENU = "HELP_MENU"
    GREETING_OR_RETURN = "GREETING_OR_RETURN"
    AFFIRMATIVE_CONTEXTUAL = "AFFIRMATIVE_CONTEXTUAL"
    TEXT_EXPERIENCE = "TEXT_EXPERIENCE"
    JOB_TEXT_INTAKE = "JOB_TEXT_INTAKE"
    JOB_URL_INTAKE = "JOB_URL_INTAKE"
    UNKNOWN = "UNKNOWN"


def normalize_text(text: str) -> str:
    """Normalizes Brazilian Portuguese text: lowercases, removes accents, strips whitespace,
    preserving colons and equals signs (for 'editar fato 1: valor' or 'fato 1 = valor').
    """
    text = (text or "").lower().strip()
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    clean = re.sub(r"[^\w\s:=]", " ", norm)
    return " ".join(clean.split())


def is_pasted_job_text_normalized(clean: str, raw_len: int) -> bool:
    """Detects whether clean normalized text represents a pasted job description."""
    if (
        clean.startswith("vaga ")
        or clean.startswith("oportunidade ")
        or clean.startswith("vaga:")
        or clean.startswith("oportunidade:")
    ):
        return True
    return raw_len > 80 and any(
        t in clean
        for t in (
            "requisitos",
            "responsabilidades",
            "beneficios",
            "sobre a vaga",
            "atribuicoes",
            "qualificacoes",
            "faixa salarial",
            "regime clt",
            "regime pj",
            "modelo hibrido",
            "modelo remoto",
        )
    )


def is_candidate_text_experience(clean: str, raw_len: int) -> bool:
    """Detects whether clean text describes a candidate's background, skills, or experience."""
    if is_pasted_job_text_normalized(clean, raw_len):
        return False
    exp_markers = (
        "tenho experiencia",
        "minha experiencia",
        "minhas experiencias",
        "anos de experiencia",
        "trabalho como",
        "trabalhei como",
        "trabalho na",
        "trabalhei na",
        "sou desenvolvedor",
        "sou desenvolvedora",
        "sou engenheiro",
        "sou engenheira",
        "sou analista",
        "minhas habilidades",
        "minhas competencias",
        "meu historico",
        "atuo como",
        "atuo na area",
        "formado em",
        "graduado em",
    )
    if any(m in clean for m in exp_markers):
        return True
    return bool(re.search(r"\b\d+\s+anos\s+(?:de\s+)?(?:experiencia|atuacao|carreira)\b", clean))


def extract_single_action_details(clean: str) -> tuple[str, int | None, str | None]:
    """Extracts (action, index, edited_value) from a single-item fact action.
    Returns:
        ("edit", index, new_value)
        ("reject", index, None)
        ("accept", index, None)
        ("unknown", None, None)
    """
    # Edit pattern: editar fato 1: Dev Python / muda o 1 para Dev Python
    m_edit = re.search(
        r"(?:edit\w*|corrig\w*|mud\w*)\s+(?:o\s+|a\s+|do\s+|da\s+)?(?:fato\s+|item\s+)?#?(\d+)\s*(?:[:=]|para)\s*(.+)$",
        clean,
    )
    if m_edit:
        return ("edit", int(m_edit.group(1)), m_edit.group(2).strip())

    # Reject pattern: rejeita o 2 / descarta fato 3 / tira o fato 2 fora
    m_rej = re.search(
        r"(?:rejeit\w*|descart\w*|ignor\w*|tir\w*|remov\w*|recus\w*)\s+(?:o\s+|a\s+|do\s+|da\s+)?(?:fato\s+|item\s+)?#?(\d+)",
        clean,
    )
    if m_rej:
        return ("reject", int(m_rej.group(1)), None)

    # Accept pattern: aceita o 1 / confirma o 2 / aprova o 3
    m_acc = re.search(
        r"(?:aceit\w*|aprov\w*|confirm\w*)\s+(?:o\s+|a\s+|do\s+|da\s+)?(?:fato\s+|item\s+)?#?(\d+)",
        clean,
    )
    if m_acc:
        return ("accept", int(m_acc.group(1)), None)

    return ("unknown", None, None)


def extract_skills_from_text(text: str) -> list[str]:
    """Extracts identified technology skills from free text."""
    lower = text.lower()
    found: list[str] = []
    for skill in COMMON_TECH_SKILLS:
        skill_clean = skill.lower()
        if re.search(r"\b" + re.escape(skill_clean) + r"\b", lower):
            found.append(skill)
    return found


def classify_conversational_intent(raw: str) -> ConversationalIntent:
    """Classifies an inbound Brazilian Portuguese text into a ConversationalIntent.
    Ensures safe precedence (e.g. deletion before fact confirmation; job inquiry before recs).
    """
    clean = normalize_text(raw)
    raw_len = len(raw.strip())
    if not clean:
        return ConversationalIntent.UNKNOWN

    # 1. URL check
    if "http://" in raw or "https://" in raw:
        return ConversationalIntent.JOB_URL_INTAKE

    # 2. Deletion (LGPD) — must take precedence over privacy and fact confirmation
    del_terms = (
        "excluir meus dados",
        "excluir dados",
        "exclui meus dados",
        "exclui dados",
        "apagar meus dados",
        "apagar dados",
        "apaga meus dados",
        "apaga dados",
        "deletar meus dados",
        "deletar dados",
        "deleta meus dados",
        "deleta dados",
        "remover meus dados",
        "remover dados",
        "remove meus dados",
        "remove dados",
        "cancelar cadastro",
        "apagar tudo",
        "apaga tudo",
        "deleta tudo",
        "deletar tudo",
        "deletar minha conta",
        "apagar minha conta",
        "deleta minha conta",
        "apaga minha conta",
        "confirmar exclusao",
    )
    if any(t in clean for t in del_terms) or clean in (
        "excluir",
        "deletar",
        "apagar conta",
        "apaga tudo",
    ):
        return ConversationalIntent.DELETION_REQUEST

    # 3. Privacy (LGPD) — must not trigger on pasted job text containing incidental 'politica'
    priv_terms = (
        "privacidade",
        "lgpd",
        "politica de privacidade",
        "protecao de dados",
        "termos de privacidade",
        "meus dados",
        "como tratam meus dados",
    )
    if any(t in clean for t in priv_terms) and not is_pasted_job_text_normalized(clean, raw_len):
        return ConversationalIntent.PRIVACY_INQUIRY

    # 4. Fact Reject All
    rej_terms = (
        "rejeitar todos",
        "descartar todos",
        "ignorar todos",
        "rejeitar fatos",
        "recusar todos",
        "nao quero nenhum",
        "rejeita tudo",
        "descarta tudo",
        "joga fora tudo",
        "nao curti joga fora",
        "nao quero nada disso",
    )
    if any(t in clean for t in rej_terms):
        return ConversationalIntent.FACT_REJECT_ALL

    # 5. Fact Single Action (reject/accept/edit with number)
    if re.search(
        r"\b(rejeit|descart|ignor|tir|remov|recus|aceit|aprov|confirm|edit|corrig|mud)\w*\s+(?:o\s+|a\s+|do\s+|da\s+)?(?:fato\s+|item\s+)?#?(\d+)",
        clean,
    ):
        return ConversationalIntent.FACT_SINGLE_ACTION

    # 6. Fact Confirm All
    conf_terms = (
        "confirmar todos",
        "confirmar fatos",
        "confirmar fato",
        "aceitar todos",
        "pode confirmar",
        "pode salvar",
        "salvar informacoes",
        "salvar dados",
        "salvar perfil",
        "ta certo",
        "tudo certo",
        "tudo ok",
        "perfeito",
        "ta perfeito",
        "esta certo",
        "aprovar todos",
        "aprova tudo",
        "aceito tudo",
        "aceitar tudo",
        "pode salvar tudo",
        "show de bola salva",
        "blz pode confirmar",
        "pode cadastrar",
        "salvar informacao",
        "pode seguir",
    )
    if any(t in clean for t in conf_terms) or clean in (
        "confirmar",
        "aceito",
        "aprovar",
        "salvar",
        "confirmo",
        "ok salvar",
    ):
        return ConversationalIntent.FACT_CONFIRM_ALL

    # 7. Application Status Query ('como estão minhas vagas?', 'onde me candidatei?')
    status_query_terms = (
        "minhas candidaturas",
        "status da candidatura",
        "status das candidaturas",
        "acompanhar candidaturas",
        "onde me candidatei",
        "quais vagas me candidatei",
        "minhas vagas aplicadas",
    )
    if any(t in clean for t in status_query_terms) or (
        clean.startswith("como est") and "candidatura" in clean
    ):
        return ConversationalIntent.APPLICATION_STATUS_QUERY

    # 8. Application Status Update ('já me candidatei')
    status_upd_terms = (
        "ja me candidatei",
        "me candidatei",
        "acabei de aplicar",
        "acabei de me candidatar",
        "candidatura enviada",
        "candidatura realizada",
        "candidatura feita",
        "ja enviei o curriculo",
        "ja enviei",
        "enviei meu curriculo",
        "mandei o curriculo",
        "ja mandei",
        "marcar aplicada",
        "marcar como enviada",
        "ja apliquei",
        "mandei cv",
    )
    if any(t in clean for t in status_upd_terms):
        return ConversationalIntent.APPLICATION_STATUS_UPDATE

    # 9. Match Explanation ('analisa essa vaga', 'o que acha da vaga?')
    # Takes precedence over general recommendations
    match_terms = (
        "analisa",
        "analisar",
        "vale a pena",
        "explicar match",
        "explica a vaga",
        "explica essa vaga",
        "o que voce acha dessa vaga",
        "o que achou dessa vaga",
        "o que acha da vaga",
        "o que acha",
        "qual meu match",
        "qual o meu match",
        "quanto deu meu match",
        "tenho chance",
        "tenho chances",
        "sou compativel",
        "pontos fortes e fracos",
        "da pra explicar",
        "me da uma luz",
        "sera que tenho chance",
    )
    if any(t in clean for t in match_terms):
        return ConversationalIntent.MATCH_EXPLANATION

    # 10. Reminder / Follow-up
    rem_terms = (
        "me lembra",
        "lembrete",
        "follow up",
        "followup",
        "quando devo cobrar",
        "avisa daqui",
        "agendar lembrete",
        "me da um toque",
        "me avisa",
    )
    if any(t in clean for t in rem_terms):
        return ConversationalIntent.REMINDER_FOLLOWUP

    # 11. Application Request ('quero me candidatar', 'curti essa vaga, como faço?')
    # Takes precedence over general recommendations
    apply_terms = (
        "quero me candidatar",
        "quero me candidata",
        "me candidatar",
        "me candidata",
        "fazer candidatura",
        "curti essa vaga",
        "curti a vaga",
        "gostei dessa vaga",
        "gostei da vaga",
        "me ajuda a aplicar",
        "ajuda a aplicar",
        "me ajuda na candidatura",
        "quero tentar essa oportunidade",
        "tentar essa vaga",
        "tentar essa oportunidade",
        "faz minha candidatura",
        "faca minha candidatura",
        "preparar candidatura",
        "vamos tentar essa",
        "vamos aplicar",
        "bora aplicar",
        "bora se candidatar",
        "como me candidato",
        "como me inscrevo",
        "como aplicar",
        "como concorrer",
        "gerar kit",
        "monta meu kit",
        "preparar kit",
        "gerar application kit",
        "manda bala",
        "quero essa vaga",
        "gostei quero essa",
        "pode aplicar",
        "aplica pra mim",
        "inscrever nessa vaga",
        "quero me inscrever",
        "como entrar nessa vaga",
    )
    if any(t in clean for t in apply_terms) or (
        ("curti" in clean or "gostei" in clean)
        and ("como faco" in clean or "como aplicar" in clean or "como que faz" in clean)
    ):
        return ConversationalIntent.APPLICATION_REQUEST

    # 12. Recommendations (General job search)
    rec_terms = (
        "vagas recomendadas",
        "recomendar vagas",
        "recomendacoes",
        "quais vagas tem",
        "ver vagas",
        "outras vagas",
        "tem mais vagas",
        "oportunidades disponiveis",
        "novas vagas",
        "buscar vagas",
        "ver oportunidades",
    )
    if any(t in clean for t in rec_terms) or clean in (
        "vagas",
        "vaga",
        "oportunidades",
        "oportunidade",
    ):
        return ConversationalIntent.JOB_RECOMMENDATIONS

    # 13. Affirmative (Contextual continuity)
    aff_terms = (
        "sim",
        "quero",
        "bora",
        "pode ser",
        "com certeza",
        "manda",
        "por favor",
        "claro",
        "faz isso",
        "beleza",
        "combinado",
        "vamos la",
    )
    if clean in aff_terms or any(
        clean.startswith(t + " ") for t in ("sim", "com certeza", "por favor")
    ):
        return ConversationalIntent.AFFIRMATIVE_CONTEXTUAL

    # 14. Help
    help_terms = (
        "ajuda",
        "help",
        "socorro",
        "o que voce faz",
        "como funciona",
        "o que voce pode fazer",
        "comandos",
        "menu",
        "opcoes",
        "como que mexe",
    )
    if any(t in clean for t in help_terms):
        return ConversationalIntent.HELP_MENU

    # 15. Greeting or Return
    greet_terms = (
        "oi",
        "ola",
        "bom dia",
        "boa tarde",
        "boa noite",
        "e ai",
        "fala tapjob",
        "voltei",
        "opa",
        "oie",
        "hey",
        "fala tu",
        "meu consagrado",
    )
    if clean in greet_terms or any(clean.startswith(t + " ") for t in greet_terms):
        return ConversationalIntent.GREETING_OR_RETURN

    # 16. Pasted Job Description
    if is_pasted_job_text_normalized(clean, raw_len):
        return ConversationalIntent.JOB_TEXT_INTAKE

    # 17. Candidate text experience intake
    if is_candidate_text_experience(clean, raw_len):
        return ConversationalIntent.TEXT_EXPERIENCE

    return ConversationalIntent.UNKNOWN
