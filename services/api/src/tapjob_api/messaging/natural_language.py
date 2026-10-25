"""Natural Language Understanding and Conversational Intent Engine for TapJob Messaging.

Architecture:
Hybrid deterministic conversational intent router with contextual disambiguation
and safe clarification fallback.

Layered pipeline:
1. Untrusted NL Input ->
2. Conversational Understanding (Semantic Interpretation with ConversationContext) ->
3. Structured Typed Result (SemanticInterpretation with intent/confidence/entities) ->
4. Deterministic Validation / Policy Gate ->
5. Safe Domain Action.

Supports colloquial, formal, informal, slang, and typo variations of Brazilian Portuguese (PT-BR).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

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

KNOWN_ATS_DOMAINS: frozenset[str] = frozenset(
    {
        "gupy.io",
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "workable.com",
        "vagas.com.br",
        "catho.com.br",
        "infojobs.com.br",
        "trampos.co",
        "kenoby.com",
        "bamboohr.com",
        "rippling.com",
        "smartrecruiters.com",
        "indeed.com",
        "glassdoor.com",
        "jobvite.com",
        "breezy.hr",
        "recruitee.com",
    }
)

KNOWN_PORTFOLIO_DOMAINS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "behance.net",
        "dribbble.com",
        "notion.site",
        "readcv.com",
        "portfoliobox.net",
    }
)


class IntentConfidence(StrEnum):
    """Confidence levels for conversational semantic interpretation."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    AMBIGUOUS = "AMBIGUOUS"


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
    PROFILE_QUERY = "PROFILE_QUERY"
    JOB_TEXT_INTAKE = "JOB_TEXT_INTAKE"
    JOB_URL_INTAKE = "JOB_URL_INTAKE"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ConversationContext:
    """Conversational and domain state for context-aware interpretation."""

    has_active_profile: bool = False
    profile_version: int = 0
    has_pending_fact_review: bool = False
    pending_facts_count: int = 0
    has_target_job: bool = False
    target_job_title: str | None = None
    target_job_company: str | None = None
    latest_application_status: str | None = None
    last_assistant_prompt: str | None = None
    last_routed_intent: ConversationalIntent | None = None


@dataclass(frozen=True)
class SemanticInterpretation:
    """Structured result of conversational semantic understanding."""

    intent: ConversationalIntent
    confidence: IntentConfidence
    extracted_entities: dict[str, Any] = field(default_factory=dict)
    clarification_question: str | None = None
    context_used: str | None = None


def normalize_text(text: str) -> str:
    """Normalizes Brazilian Portuguese text: lowercases, removes accents, strips whitespace,
    preserving colons and equals signs (for 'editar fato 1: valor' or 'fato 1 = valor').
    """
    text = (text or "").lower().strip()
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    clean = re.sub(r"[^\w\s:=]", " ", norm)
    return " ".join(clean.split())


def extract_urls(text: str) -> list[str]:
    """Extracts HTTP and HTTPS URLs from raw text."""
    url_pattern = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
    matches = url_pattern.findall(text or "")
    # Clean trailing punctuation commonly appended in chat (e.g. '.', ',', ')')
    cleaned_urls = []
    for u in matches:
        cleaned = re.sub(r"[.,;!?\)]+$", "", u)
        if cleaned:
            cleaned_urls.append(cleaned)
    return cleaned_urls


def classify_url_intent(url: str, full_text: str = "") -> SemanticInterpretation:
    """Classifies the semantic intent of an inbound URL.

    Distinguishes:
    - JOB_POSTING (ATS domains or job paths) -> JOB_URL_INTAKE
    - PORTFOLIO_OR_PROFILE (GitHub, LinkedIn personal profile, Behance) -> TEXT_EXPERIENCE
    - PRIVACY_POLICY (Privacy, LGPD, terms) -> PRIVACY_INQUIRY
    - UNKNOWN -> CLARIFICATION_NEEDED with safe question.
    """
    try:
        parsed = urlparse(url)
        netloc = (parsed.netloc or "").lower().split(":")[0]
        path = (parsed.path or "").lower()
    except Exception:
        return SemanticInterpretation(
            intent=ConversationalIntent.CLARIFICATION_NEEDED,
            confidence=IntentConfidence.AMBIGUOUS,
            extracted_entities={"invalid_url": url},
            clarification_question=(
                "Não consegui acessar esse endereço. "
                "Poderia reenviar o link completo da vaga ou do seu currículo?"
            ),
        )

    # 1. ATS and Known Job Boards
    is_ats_domain = any(netloc == d or netloc.endswith("." + d) for d in KNOWN_ATS_DOMAINS)
    is_job_path = any(
        seg in path
        for seg in (
            "/jobs/",
            "/job/",
            "/vagas/",
            "/vaga/",
            "/careers/",
            "/carreiras/",
            "/oportunidades/",
            "/apply/",
            "/candidatura/",
            "/positions/",
        )
    )
    is_linkedin_job = "linkedin.com" in netloc and ("/jobs/" in path or "/view/" in path)

    clean_text = normalize_text(full_text)
    text_has_job_cues = any(
        cue in clean_text for cue in ("vaga", "oportunidade", "cargo", "requisitos", "salario")
    )

    if is_ats_domain or is_job_path or is_linkedin_job or text_has_job_cues:
        conf = IntentConfidence.HIGH if (is_ats_domain or is_job_path) else IntentConfidence.MEDIUM
        return SemanticInterpretation(
            intent=ConversationalIntent.JOB_URL_INTAKE,
            confidence=conf,
            extracted_entities={"job_url": url},
            context_used="ats_domain_or_job_path",
        )

    # 2. Portfolio or Personal Profile
    is_portfolio_domain = any(
        netloc == d or netloc.endswith("." + d) for d in KNOWN_PORTFOLIO_DOMAINS
    )
    is_linkedin_profile = "linkedin.com" in netloc and "/in/" in path
    text_has_profile_cues = any(
        cue in clean_text
        for cue in ("portfolio", "meu github", "meu perfil", "meu linkedin", "meu curriculo")
    )

    if is_portfolio_domain or is_linkedin_profile or text_has_profile_cues:
        conf = (
            IntentConfidence.HIGH
            if (is_portfolio_domain or is_linkedin_profile)
            else IntentConfidence.MEDIUM
        )
        return SemanticInterpretation(
            intent=ConversationalIntent.TEXT_EXPERIENCE,
            confidence=conf,
            extracted_entities={"portfolio_url": url},
            context_used="portfolio_or_profile_domain",
        )

    # 3. Privacy / Legal Links
    is_privacy_path = any(
        kw in path for kw in ("privacidade", "privacy", "lgpd", "termos", "terms")
    )
    if is_privacy_path or "privacidade" in clean_text or "lgpd" in clean_text:
        return SemanticInterpretation(
            intent=ConversationalIntent.PRIVACY_INQUIRY,
            confidence=IntentConfidence.HIGH,
            extracted_entities={"privacy_url": url},
            context_used="privacy_policy_url",
        )

    # 4. Generic / Unknown URL Fallback -> Request Clarification
    return SemanticInterpretation(
        intent=ConversationalIntent.CLARIFICATION_NEEDED,
        confidence=IntentConfidence.AMBIGUOUS,
        extracted_entities={"unknown_url": url},
        clarification_question=(
            "Recebi seu link! Para te atender melhor: este link é de uma vaga de emprego "
            "ou do seu perfil/portfólio profissional? (Envie 'vaga' ou 'perfil')."
        ),
    )


def is_pasted_job_text_normalized(clean: str, raw_len: int = 0) -> bool:
    """Detects whether clean normalized text represents a pasted job description."""
    norm = normalize_text(clean)
    effective_len = raw_len or len(clean)
    if (
        norm.startswith("vaga ")
        or norm.startswith("oportunidade ")
        or norm.startswith("vaga:")
        or norm.startswith("oportunidade:")
    ):
        return True
    return effective_len > 80 and any(
        t in norm
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


def is_candidate_text_experience(clean: str, raw_len: int = 0) -> bool:
    """Detects whether clean text describes a candidate's background, skills, or experience."""
    norm = normalize_text(clean)
    effective_len = raw_len or len(clean)
    if is_pasted_job_text_normalized(norm, effective_len):
        return False
    exp_markers = (
        "tenho experiencia",
        "minha experiencia",
        "minhas experiencias",
        "anos de experiencia",
        "trabalho como",
        "trabalhei como",
        "trabalho com",
        "trabalhei com",
        "trabalho usando",
        "trabalhei usando",
        "trabalho na",
        "trabalhei na",
        "atuo com",
        "atuei com",
        "atuo como",
        "atuo na area",
        "uso profissionalmente",
        "tenho dominio de",
        "tenho dominio em",
        "minha stack e",
        "minha stack:",
        "minha stack atual",
        "venho trabalhando com",
        "venho atuando com",
        "experiencia com",
        "experiencia em",
        "sou desenvolvedor",
        "sou desenvolvedora",
        "sou engenheiro",
        "sou engenheira",
        "sou analista",
        "minhas habilidades",
        "minhas competencias",
        "meu historico",
        "formado em",
        "graduado em",
    )
    if any(m in norm for m in exp_markers):
        return True
    return bool(
        re.search(r"\b\d+\s+anos\s+(?:de\s+)?(?:experiencia|atuacao|carreira)\b", norm)
        or re.search(r"\b(?:trabalh\w*|atu\w*)\s+com\b.*?\b(?:ha|a)?\s*\d+\s+anos\b", norm)
    )


def extract_single_action_details(clean: str) -> tuple[str, int | None, str | None]:
    """Extracts (action, index, edited_value) from a single-item fact action.

    Returns:
        ("edit", index, new_value)
        ("reject", index, None)
        ("accept", index, None)
        ("unknown", None, None)
    """
    m_edit = re.search(
        r"(?:edit\w*|corrig\w*|mud\w*)\s+(?:o\s+|a\s+|do\s+|da\s+)?(?:fato\s+|item\s+)?#?(\d+)\s*(?:[:=]|para)\s*(.+)$",
        clean,
    )
    if m_edit:
        return ("edit", int(m_edit.group(1)), m_edit.group(2).strip())

    m_rej = re.search(
        r"(?:rejeit\w*|descart\w*|ignor\w*|tir\w*|remov\w*|recus\w*)\s+(?:o\s+|a\s+|do\s+|da\s+)?(?:fato\s+|item\s+)?#?(\d+)",
        clean,
    )
    if m_rej:
        return ("reject", int(m_rej.group(1)), None)

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


def parse_natural_reminder_time(
    text: str,
    base_time: datetime | None = None,
) -> tuple[datetime | None, str | None]:
    """Deterministically extracts relative or named reminder times from PT-BR natural text.

    Supported patterns:
    - 'daqui (a)? X minuto(s)' -> base_time + X minutes
    - 'daqui (a)? X hora(s)' -> base_time + X hours
    - 'daqui (a)? X dia(s)' -> base_time + X days
    - 'amanhã' / 'amanha' -> base_time + 1 day
    - 'daqui a pouco' -> base_time + 1 hour
    - 'semana que vem' / 'próxima semana' -> base_time + 7 days
    """
    if base_time is None:
        base_time = datetime.now(UTC)

    clean = normalize_text(text)

    # 1. 'daqui (a)? X minuto(s)'
    m = re.search(r"\bdaqui\s+(?:a\s+)?(\d+)\s*minuto", clean)
    if m:
        mins = int(m.group(1))
        unit = "minuto" if mins == 1 else "minutos"
        return base_time + timedelta(minutes=mins), f"daqui a {mins} {unit}"

    # 2. 'daqui (a)? X hora(s)'
    m = re.search(r"\bdaqui\s+(?:a\s+)?(\d+)\s*hora", clean)
    if m:
        hrs = int(m.group(1))
        unit = "hora" if hrs == 1 else "horas"
        return base_time + timedelta(hours=hrs), f"daqui a {hrs} {unit}"

    # 3. 'daqui (a)? X dia(s)'
    m = re.search(r"\bdaqui\s+(?:a\s+)?(\d+)\s*dia", clean)
    if m:
        days = int(m.group(1))
        unit = "dia" if days == 1 else "dias"
        return base_time + timedelta(days=days), f"daqui a {days} {unit}"

    # 4. 'amanhã' / 'amanha'
    if "amanha" in clean or "amanhã" in clean:
        return base_time + timedelta(days=1), "amanhã"

    # 5. 'daqui a pouco'
    if "daqui a pouco" in clean:
        return base_time + timedelta(hours=1), "daqui a 1 hora"

    # 6. 'semana que vem' / 'próxima semana'
    if "semana que vem" in clean or "proxima semana" in clean:
        return base_time + timedelta(days=7), "na próxima semana"

    return None, None


def is_waiting_for_reminder_time_prompt(prompt: str | None) -> bool:
    """Checks whether the last assistant prompt indicated waiting for reminder time definition."""
    if not prompt:
        return False
    p_norm = normalize_text(prompt)
    if "lembrete agendado com sucesso" in p_norm:
        return False
    if "lembrete" in p_norm:
        inquiry_patterns = (
            "para quando",
            "quando voce gostaria",
            "momento desejado",
            "agendar esse lembrete",
            "agendar o lembrete",
            "definir o horario",
            "horario do lembrete",
        )
        return any(pat in p_norm for pat in inquiry_patterns)
    return False


def is_explicit_privacy_inquiry(clean: str, raw_len: int = 0) -> bool:
    """Detects whether clean normalized text represents an explicit privacy/LGPD inquiry.

    Must NOT match confirmation messages containing 'dados' (e.g. 'pode salvar meus dados'),
    nor pasted job postings mentioning privacy policies.
    """
    if is_pasted_job_text_normalized(clean, raw_len):
        return False

    # Standalone or explicit privacy/LGPD terms
    explicit_terms = (
        "privacidade",
        "lgpd",
        "politica de privacidade",
        "termos de privacidade",
        "termo de privacidade",
        "protecao de dados",
        "seguranca dos dados",
        "seguranca de dados",
        "lei geral de protecao",
        "direitos lgpd",
    )
    if any(t in clean for t in explicit_terms):
        return True

    # Semantic inquiries / questions about personal data:
    # 1. Asking WHERE data is saved / kept / stored
    if re.search(r"\bonde\b.*?\b(?:salv|guard|armazen|fic|coloc)\w*\b.*?\bdados\b", clean):
        return True

    # 2. Asking HOW data is treated / used / processed / protected
    if re.search(r"\bcomo\b.*?\b(?:trat|us|process|proteg|cuid)\w*\b.*?\bdados\b", clean):
        return True
    if "como tratam meus dados" in clean or "como usam meus dados" in clean:
        return True

    # 3. Asking WHICH data is stored / collected / held
    if re.search(r"\bquais\b.*?\bdados\b.*?\b(?:guard|colet|armazen|salv|tem|peg|us)\w*\b", clean):
        return True

    # 4. Asking WHY data is needed / requested
    if re.search(r"\bpor\s+que\b.*?\b(?:precis|ped|quer|us)\w*\b.*?\bdados\b", clean):
        return True

    # 5. Asking HOW LONG data is retained / kept
    if re.search(
        r"\b(?:quanto\s+tempo|por\s+quanto\s+tempo)\b.*?\b(?:guard|armazen|mant|fic)\w*\b.*?\bdados\b",
        clean,
    ):
        return True

    # 6. Asking about data security / safety
    if (
        re.search(r"\bdados\b.*?\b(?:seguro|protegido)s?\b", clean)
        or "dados estao seguros" in clean
    ):
        return True

    # 7. Asking about data sharing / third party access
    if re.search(r"(?:compartilh\w*|quem\s+tem\s+acesso)\b.*?\bdados\b", clean):
        return True

    # 8. Inquiring about data saving/storage authorization (e.g. "você pode salvar meus dados?")
    return bool(
        re.search(
            r"\b(?:voce|voces|vcs?|da\s+pra)\b.*?\b(?:pode|podem|salv|guard)\w*\b.*?\bdados\b",
            clean,
        )
    )


def interpret_conversational_message(
    text: str,
    context: ConversationContext | None = None,
) -> SemanticInterpretation:
    """Interprets untrusted inbound conversational message into a structured SemanticInterpretation.

    Execution Pipeline:
    1. Normalization & Sanity
    2. Deterministic Privacy & LGPD Gates (always take precedence)
    3. Context-Aware URL Classification
    4. Deterministic Fact Single Actions (indexed edits/accepts/rejects)
    5. Fact Confirmation / Rejection
    6. Application Query & Lifecycle Updates
    7. Match Explanations & Compatibility Inquiries
    8. Application Kit Requests
    9. Follow-up Reminders
    10. Job Recommendations Inquiries
    11. Contextual Affirmative / Casual Follow-ups (using ConversationContext)
    12. Help & Commands
    13. Greetings & Return Welcome
    14. Job Description & Candidate Experience Intake
    15. Ambiguity & Clarification Fallback
    """
    clean = normalize_text(text)
    raw_len = len(text.strip())
    if not clean:
        return SemanticInterpretation(
            intent=ConversationalIntent.UNKNOWN,
            confidence=IntentConfidence.LOW,
        )

    # 1. Deterministic LGPD Deletion Gate (Highest Precedence)
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
        return SemanticInterpretation(
            intent=ConversationalIntent.DELETION_REQUEST,
            confidence=IntentConfidence.HIGH,
            context_used="lgpd_deletion_gate",
        )

    # 2. Deterministic LGPD Privacy Inquiry Gate
    if is_explicit_privacy_inquiry(clean, raw_len):
        return SemanticInterpretation(
            intent=ConversationalIntent.PRIVACY_INQUIRY,
            confidence=IntentConfidence.HIGH,
            context_used="lgpd_privacy_gate",
        )

    # 3. Context-Aware URL Classification (Finding 3)
    urls = extract_urls(text)
    if urls:
        # Evaluate primary URL with context
        url_interp = classify_url_intent(urls[0], full_text=text)
        return url_interp

    # 4. Fact Single Action (e.g. 'rejeita o 2', 'editar fato 1: Python', 'aceita o 3')
    action, idx, val = extract_single_action_details(clean)
    if action != "unknown" and idx is not None:
        return SemanticInterpretation(
            intent=ConversationalIntent.FACT_SINGLE_ACTION,
            confidence=IntentConfidence.HIGH,
            extracted_entities={"action": action, "index": idx, "value": val},
            context_used="single_fact_action_pattern",
        )

    # 5. Fact Rejection (All)
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
        return SemanticInterpretation(
            intent=ConversationalIntent.FACT_REJECT_ALL,
            confidence=IntentConfidence.HIGH,
            context_used="reject_all_pattern",
        )

    # 6. Fact Confirmation (All)
    has_pending_review = context is not None and context.has_pending_fact_review

    # Unconditional confirmation terms (unambiguous commands with or without active review context)
    unambiguous_conf_terms = (
        "confirmar todos",
        "confirmar fatos",
        "confirmar fato",
        "confirmar meus dados",
        "confirmar informacoes",
        "aceitar todos",
        "aceitar sugestoes",
        "aceitar tudo",
        "aceito tudo",
        "aprovar todos",
        "aprovar sugestoes",
        "aprovar fatos",
        "aprova tudo",
        "meus dados estao certos",
        "meus dados estao corretos",
    )
    is_unambiguous = any(t in clean for t in unambiguous_conf_terms) or clean in (
        "confirmar",
        "confirmo",
        "aprovar",
        "aceito",
    )

    # Contextual confirmation phrases (require active pending fact review)
    contextual_conf_terms = (
        "sim",
        "pode",
        "claro",
        "confirma",
        "pode confirmar",
        "pode adicionar",
        "pode incluir",
        "pode colocar",
        "esta certo",
        "ta certo",
        "isso mesmo",
        "isso ai",
        "exato",
        "isso",
        "tudo certo, pode salvar meus dados",
        "tudo certo pode salvar meus dados",
        "tudo certo pode salvar",
        "tudo certo, pode salvar",
        "pode salvar meus dados",
        "salvar meus dados",
        "pode salvar tudo",
        "salvar tudo",
        "pode salvar",
        "salvar dados",
        "salvar perfil",
        "salvar informacoes",
        "salvar informacao",
        "tudo certo",
        "tudo ok",
        "perfeito",
        "ta perfeito",
        "show de bola salva",
        "blz pode confirmar",
        "pode cadastrar",
        "pode seguir",
        "salvar",
        "ok salvar",
        "ta ok",
        "adicionar",
        "concordo",
        "aprovo",
        "pode atualizar",
        "atualiza",
        "atualizar",
    )
    is_contextual_conf = has_pending_review and any(t in clean for t in contextual_conf_terms)

    if is_unambiguous or is_contextual_conf:
        ctx_tag = "context:pending_fact_review" if has_pending_review else "confirm_all_pattern"
        return SemanticInterpretation(
            intent=ConversationalIntent.FACT_CONFIRM_ALL,
            confidence=IntentConfidence.HIGH,
            context_used=ctx_tag,
        )

    # 6.5 Profile Information Query (Finding 5)
    profile_query_terms = (
        "o que voce sabe sobre meu perfil",
        "o que voce sabe sobre mim",
        "o que sabe sobre meu perfil",
        "o que sabe sobre mim",
        "como esta meu perfil",
        "como ta meu perfil",
        "o que tem no meu perfil",
        "o que tem em meu perfil",
        "quais informacoes profissionais voce tem minhas",
        "quais informacoes voce tem sobre mim",
        "quais informacoes voce tem do meu perfil",
        "quais dados voce tem sobre mim",
        "quais dados voce tem do meu perfil",
        "me mostra meu perfil",
        "mostrar meu perfil",
        "mostra meu perfil",
        "ver meu perfil",
        "ver perfil",
        "consultar meu perfil",
        "consultar perfil",
        "informacoes do meu perfil",
        "dados do meu perfil",
    )
    if any(t in clean for t in profile_query_terms) or (
        "perfil" in clean
        and any(
            w in clean
            for w in (
                "como esta",
                "como ta",
                "o que tem",
                "me mostra",
                "mostra",
                "o que sabe",
            )
        )
    ):
        return SemanticInterpretation(
            intent=ConversationalIntent.PROFILE_QUERY,
            confidence=IntentConfidence.HIGH,
            context_used="profile_query_pattern",
        )

    # 7. Application Status Query
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
        return SemanticInterpretation(
            intent=ConversationalIntent.APPLICATION_STATUS_QUERY,
            confidence=IntentConfidence.HIGH,
            context_used="status_query_pattern",
        )

    # 8. Application Status Update (Candidate already submitted)
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
        return SemanticInterpretation(
            intent=ConversationalIntent.APPLICATION_STATUS_UPDATE,
            confidence=IntentConfidence.HIGH,
            context_used="status_update_pattern",
        )

    # 9. Match Explanation / Compatibility Analysis
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
        return SemanticInterpretation(
            intent=ConversationalIntent.MATCH_EXPLANATION,
            confidence=IntentConfidence.HIGH,
            context_used="match_explanation_pattern",
        )

    # 10. Reminder / Follow-up Scheduling
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
        return SemanticInterpretation(
            intent=ConversationalIntent.REMINDER_FOLLOWUP,
            confidence=IntentConfidence.HIGH,
            context_used="reminder_pattern",
        )

    # 10.1 Reminder Conversational Continuity (Turn 2 following reminder clarification prompt)
    if context is not None:
        is_pending_reminder_time = False
        if context.last_routed_intent == ConversationalIntent.REMINDER_FOLLOWUP:
            if context.last_assistant_prompt is None or is_waiting_for_reminder_time_prompt(
                context.last_assistant_prompt
            ):
                is_pending_reminder_time = True
        elif is_waiting_for_reminder_time_prompt(context.last_assistant_prompt):
            is_pending_reminder_time = True

        if is_pending_reminder_time:
            parsed_time, _ = parse_natural_reminder_time(text)
            if parsed_time is not None:
                return SemanticInterpretation(
                    intent=ConversationalIntent.REMINDER_FOLLOWUP,
                    confidence=IntentConfidence.HIGH,
                    context_used="context:pending_reminder_time_continuation",
                )

    # 11. Conversational Application Request
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
        return SemanticInterpretation(
            intent=ConversationalIntent.APPLICATION_REQUEST,
            confidence=IntentConfidence.HIGH,
            context_used="apply_request_pattern",
        )

    # 12. General Recommendations
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
        return SemanticInterpretation(
            intent=ConversationalIntent.JOB_RECOMMENDATIONS,
            confidence=IntentConfidence.HIGH,
            context_used="job_recommendations_pattern",
        )

    # 13. Contextual Continuity / Affirmative & Casual Follow-ups
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
        "essa parece boa",
        "gostei dessa",
        "curti essa",
        "curti",
        "gostei",
    )
    is_affirmative_or_casual = clean in aff_terms or any(
        clean.startswith(t + " ") for t in ("sim", "com certeza", "por favor", "bora", "claro")
    )

    if is_affirmative_or_casual:
        # Check context for resolution
        if context is not None:
            # Context Case A: Pending Fact Review waiting for confirmation
            if context.has_pending_fact_review:
                return SemanticInterpretation(
                    intent=ConversationalIntent.FACT_CONFIRM_ALL,
                    confidence=IntentConfidence.HIGH,
                    context_used="context:pending_fact_review",
                )

            # Context Case B: Active Target Job present
            if context.has_target_job:
                return SemanticInterpretation(
                    intent=ConversationalIntent.APPLICATION_REQUEST,
                    confidence=IntentConfidence.HIGH,
                    extracted_entities={
                        "target_job_title": context.target_job_title,
                        "target_job_company": context.target_job_company,
                    },
                    context_used=f"context:target_job:{context.target_job_title}",
                )

            # Context Case C: Assistant recently prompted about application kit or facts
            if context.last_assistant_prompt:
                prompt_lower = context.last_assistant_prompt.lower()
                if (
                    "quero me candidatar" in prompt_lower
                    or "application kit" in prompt_lower
                    or "oportunidade?" in prompt_lower
                ):
                    return SemanticInterpretation(
                        intent=ConversationalIntent.APPLICATION_REQUEST,
                        confidence=IntentConfidence.HIGH,
                        context_used="context:last_prompt_kit",
                    )
                if "confirmar" in prompt_lower and (
                    "fato" in prompt_lower
                    or "competencia" in prompt_lower
                    or "informacoes" in prompt_lower
                ):
                    return SemanticInterpretation(
                        intent=ConversationalIntent.FACT_CONFIRM_ALL,
                        confidence=IntentConfidence.HIGH,
                        context_used="context:last_prompt_confirm",
                    )

        # Ambiguous casual input without decisive context
        if clean in ("essa parece boa", "gostei dessa", "curti essa"):
            return SemanticInterpretation(
                intent=ConversationalIntent.CLARIFICATION_NEEDED,
                confidence=IntentConfidence.AMBIGUOUS,
                clarification_question=(
                    "Fico feliz que você tenha gostado! "
                    "Envie o link ou o texto da vaga para que eu possa analisar o match "
                    "e preparar seu Application Kit."
                ),
                context_used="casual_interest_no_target_job",
            )

        return SemanticInterpretation(
            intent=ConversationalIntent.AFFIRMATIVE_CONTEXTUAL,
            confidence=IntentConfidence.MEDIUM,
            context_used="affirmative_standalone",
        )

    # 14. Help Menu & Assistance
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
        return SemanticInterpretation(
            intent=ConversationalIntent.HELP_MENU,
            confidence=IntentConfidence.HIGH,
            context_used="help_menu_pattern",
        )

    # 15. Greeting & Returning Welcome
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
        return SemanticInterpretation(
            intent=ConversationalIntent.GREETING_OR_RETURN,
            confidence=IntentConfidence.HIGH,
            context_used="greeting_pattern",
        )

    # 16. Pasted Job Description
    if is_pasted_job_text_normalized(clean, raw_len):
        return SemanticInterpretation(
            intent=ConversationalIntent.JOB_TEXT_INTAKE,
            confidence=IntentConfidence.HIGH,
            context_used="pasted_job_description",
        )

    # 17. Candidate Text Experience Intake
    if is_candidate_text_experience(clean, raw_len):
        return SemanticInterpretation(
            intent=ConversationalIntent.TEXT_EXPERIENCE,
            confidence=IntentConfidence.HIGH,
            context_used="candidate_experience_text",
        )

    # 18. Unknown Fallback
    return SemanticInterpretation(
        intent=ConversationalIntent.UNKNOWN,
        confidence=IntentConfidence.LOW,
    )


def classify_conversational_intent(
    raw: str,
    context: ConversationContext | None = None,
) -> ConversationalIntent:
    """Facade for backward-compatibility. Classifies text into ConversationalIntent."""
    return interpret_conversational_message(raw, context).intent
