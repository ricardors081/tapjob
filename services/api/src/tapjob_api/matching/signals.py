"""Signal extraction and reduction for WP2.2 Matching Engine (ADR-0014 Decisions 5 & 5B).

Pure, deterministic functions with ZERO network calls and ZERO LLM inference:
- derive_job_match_signals(job, policy_version) -> DerivedJobMatchSignals
- derive_candidate_match_signals(snapshot, policy_version, as_of_date) -> CandidateMatchSignals
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import date

from pydantic import TypeAdapter

from tapjob_api.career_profile.fact_values import (
    ExperienceFactValue,
    FactValue,
    LocationPreferenceValue,
    PreferenceFactValue,
    ProjectFactValue,
    SalaryPeriod,
    SalaryPreferenceValue,
    SkillFactValue,
    WorkAuthorizationFactValue,
    WorkplaceType,
    WorkplaceTypePreferenceValue,
)
from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.types import (
    CandidateDomainTokenSignal,
    CandidateExperienceSignal,
    CandidateLocationPreference,
    CandidateMatchSignals,
    CandidateSalaryPreference,
    CandidateSeniorityLevel,
    CandidateSkillSignal,
    CandidateWorkAuthorizationSignal,
    DerivedJobMatchSignals,
    JobSalarySignal,
    JobSeniorityLevel,
    JobWorkAuthSignal,
    WorkAuthSemanticValue,
)

# -- Normalization Helpers --


logger = logging.getLogger(__name__)


def normalize_text_token(text: str | None) -> str:
    """Lowercase and strip diacritics/accents via Unicode NFKD."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_bytes = nfkd.encode("ASCII", "ignore")
    return ascii_bytes.decode("ascii").lower().strip()


# -- Closed Tech Taxonomy & Alias Map (Policy 2026.1) --

TECH_TAXONOMY_V1: frozenset[str] = frozenset(
    {
        "airflow",
        "angular",
        "ansible",
        "aws",
        "azure",
        "c",
        "celery",
        "cpp",
        "csharp",
        "css",
        "django",
        "docker",
        "elasticsearch",
        "fastapi",
        "flask",
        "flutter",
        "gcp",
        "git",
        "go",
        "graphql",
        "hadoop",
        "html",
        "java",
        "javascript",
        "kafka",
        "kotlin",
        "kubernetes",
        "linux",
        "mongodb",
        "mysql",
        "nextjs",
        "nodejs",
        "php",
        "postgresql",
        "python",
        "rabbitmq",
        "react",
        "react_native",
        "redis",
        "rest",
        "ruby",
        "rust",
        "spark",
        "spring",
        "sql",
        "sqlite",
        "swift",
        "tailwind",
        "terraform",
        "typescript",
        "vue",
    }
)

TECH_ALIAS_MAP_V1: dict[str, str] = {
    "k8s": "kubernetes",
    "k8": "kubernetes",
    "kube": "kubernetes",
    "postgres": "postgresql",
    "psql": "postgresql",
    "golang": "go",
    "c#": "csharp",
    "c++": "cpp",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "reactjs": "react",
    "react.js": "react",
    "react native": "react_native",
    "react-native": "react_native",
    "vuejs": "vue",
    "vue.js": "vue",
    "nodejs": "nodejs",
    "node.js": "nodejs",
    "node": "nodejs",
    "next": "nextjs",
    "next.js": "nextjs",
    "tailwindcss": "tailwind",
    "elastic": "elasticsearch",
    "elastic search": "elasticsearch",
    "amazon web services": "aws",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "microsoft azure": "azure",
    "spring boot": "spring",
    "rest api": "rest",
    "restful": "rest",
    "restful api": "rest",
    "graphql api": "graphql",
}


def canonicalize_skill(raw_name: str) -> str | None:
    """Maps raw skill text to canonical taxonomy token, resolving aliases."""
    normalized = normalize_text_token(raw_name)
    if not normalized:
        return None
    # Check alias map
    resolved = TECH_ALIAS_MAP_V1.get(normalized, normalized)
    # Check taxonomy
    if resolved in TECH_TAXONOMY_V1:
        return resolved
    # Check if normalized with spaces matches alias
    collapsed = " ".join(normalized.split())
    resolved_collapsed = TECH_ALIAS_MAP_V1.get(collapsed, collapsed)
    if resolved_collapsed in TECH_TAXONOMY_V1:
        return resolved_collapsed
    return None


# -- Domain Taxonomy & Alias Map (Policy 2026.1) --

DOMAIN_TAXONOMY_V1: frozenset[str] = frozenset(
    {
        "agile",
        "ai",
        "analytics",
        "api",
        "architecture",
        "backend",
        "ci_cd",
        "cloud",
        "data",
        "database",
        "devops",
        "distributed_systems",
        "frontend",
        "fullstack",
        "infrastructure",
        "machine_learning",
        "microservices",
        "mobile",
        "observability",
        "performance",
        "qa",
        "scrum",
        "security",
        "testing",
    }
)

DOMAIN_ALIAS_MAP_V1: dict[str, str] = {
    "agil": "agile",
    "ci/cd": "ci_cd",
    "continuous integration": "ci_cd",
    "nuvem": "cloud",
    "dados": "data",
    "banco de dados": "database",
    "sistemas distribuidos": "distributed_systems",
    "infra": "infrastructure",
    "infraestrutura": "infrastructure",
    "aprendizado de maquina": "machine_learning",
    "ml": "machine_learning",
    "inteligencia artificial": "ai",
    "ia": "ai",
    "microsservicos": "microservices",
    "microsserviços": "microservices",
    "observabilidade": "observability",
    "desempenho": "performance",
    "qualidade": "qa",
    "testes": "testing",
    "seguranca": "security",
    "arquitetura": "architecture",
}


def canonicalize_domain_token(raw_text: str) -> str | None:
    normalized = normalize_text_token(raw_text)
    if not normalized:
        return None
    resolved = DOMAIN_ALIAS_MAP_V1.get(normalized, normalized)
    if resolved in DOMAIN_TAXONOMY_V1:
        return resolved
    collapsed = " ".join(normalized.split())
    resolved_collapsed = DOMAIN_ALIAS_MAP_V1.get(collapsed, collapsed)
    if resolved_collapsed in DOMAIN_TAXONOMY_V1:
        return resolved_collapsed
    return None


# -- Salary Period & Mappings --

JOB_SALARY_PERIOD_MAP: dict[str, SalaryPeriod] = {
    "month": SalaryPeriod.MONTHLY,
    "mês": SalaryPeriod.MONTHLY,
    "mes": SalaryPeriod.MONTHLY,
    "/mo": SalaryPeriod.MONTHLY,
    "monthly": SalaryPeriod.MONTHLY,
    "mensal": SalaryPeriod.MONTHLY,
    "year": SalaryPeriod.ANNUAL,
    "ano": SalaryPeriod.ANNUAL,
    "/yr": SalaryPeriod.ANNUAL,
    "annual": SalaryPeriod.ANNUAL,
    "annually": SalaryPeriod.ANNUAL,
    "anual": SalaryPeriod.ANNUAL,
    "hour": SalaryPeriod.HOURLY,
    "hora": SalaryPeriod.HOURLY,
    "/hr": SalaryPeriod.HOURLY,
    "hourly": SalaryPeriod.HOURLY,
    "por hora": SalaryPeriod.HOURLY,
}

# -- Work Authorization Answer Map --

WORK_AUTHORIZATION_ANSWER_MAP: dict[str, dict[str, WorkAuthSemanticValue]] = {
    "legally_authorized_to_work": {
        "yes": WorkAuthSemanticValue.AUTHORIZED,
        "sim": WorkAuthSemanticValue.AUTHORIZED,
        "authorized": WorkAuthSemanticValue.AUTHORIZED,
        "autorizado": WorkAuthSemanticValue.AUTHORIZED,
        "no": WorkAuthSemanticValue.NOT_AUTHORIZED,
        "nao": WorkAuthSemanticValue.NOT_AUTHORIZED,
        "unauthorized": WorkAuthSemanticValue.NOT_AUTHORIZED,
        "nao_autorizado": WorkAuthSemanticValue.NOT_AUTHORIZED,
    },
    "visa_sponsorship_required": {
        "yes": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "sim": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "true": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "requires_sponsorship": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "precisa_visto": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "no": WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP,
        "nao": WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP,
        "false": WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP,
    },
    "citizen_or_permanent_resident": {
        "yes": WorkAuthSemanticValue.AUTHORIZED,
        "sim": WorkAuthSemanticValue.AUTHORIZED,
        "citizen": WorkAuthSemanticValue.AUTHORIZED,
        "cidadao": WorkAuthSemanticValue.AUTHORIZED,
        "permanent_resident": WorkAuthSemanticValue.AUTHORIZED,
    },
    "work_authorization_status": {
        "authorized": WorkAuthSemanticValue.AUTHORIZED,
        "autorizado": WorkAuthSemanticValue.AUTHORIZED,
        "requires_sponsorship": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "precisa_visto": WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
        "unauthorized": WorkAuthSemanticValue.NOT_AUTHORIZED,
        "nao_autorizado": WorkAuthSemanticValue.NOT_AUTHORIZED,
    },
}

# -- Regex Patterns for Job Derivation --

_RE_REQUIRED_HEADINGS = re.compile(
    r"(?im)^(?:#+\s*)?(?:requisitos|requirements|o que voc[eê] precisa ter|"
    r"qualifica[cç][oõ]es necess[aá]rias|must-have|mandatory qualifications)[:\s]*(.*)$",
)
_RE_PREFERRED_HEADINGS = re.compile(
    r"(?im)^(?:#+\s*)?(?:diferenciais|desej[aá]vel|desejaveis|nice to have|"
    r"preferred qualifications|plus|o que seria legal ter)[:\s]*(.*)$",
)
_RE_OTHER_HEADINGS = re.compile(
    r"(?im)^(?:#+\s*)?(?:sobre a empresa|about us|benef[ií]cios|benefits|"
    r"responsabilidades|responsibilities|atividades|o que voc[eê] vai fazer|"
    r"etapas do processo|processo seletivo)[:\s]*(.*)$",
)

_RE_SENIORITY_INTERN = re.compile(r"(?i)\b(est[aá]gio|estagi[aá]rio|intern|internship)\b")
_RE_SENIORITY_JUNIOR = re.compile(r"(?i)\b(j[uú]nior|junior|jr|entry-level|entry level)\b")
_RE_SENIORITY_MID = re.compile(r"(?i)\b(pleno|mid|mid-level|mid level|pl)\b")
_RE_SENIORITY_SENIOR = re.compile(r"(?i)\b(s[eê]nior|senior|sr)\b")
_RE_SENIORITY_LEAD = re.compile(r"(?i)\b(lead|l[ií]der t[eé]cnico|tech lead)\b")
_RE_SENIORITY_STAFF = re.compile(r"(?i)\b(staff|principal|architect|arquiteto)\b")

_RE_AUTH_REQ = re.compile(
    r"(?i)\b("
    r"(?:must\s+be\s+)?(?:legally\s+)?authorized\s+to\s+work|"
    r"legal\s+authorization\s+to\s+work(?:\s+required)?|"
    r"autoriza[cç][aã]o\s+(?:de\s+trabalho|para\s+trabalhar)|"
    r"autorizad[oa]s?\s+(?:a|para)\s+trabalhar"
    r")\b",
)
_RE_AUTH_BOUND_JURISDICTION = re.compile(
    r"(?i)\b"
    r"(?:(?:must\s+be\s+)?(?:legally\s+)?authorized\s+to\s+work|"
    r"legal\s+authorization\s+to\s+work(?:\s+required)?|"
    r"autoriza[cç][aã]o\s+(?:de\s+trabalho|para\s+trabalhar)|"
    r"autorizad[oa]s?\s+(?:a|para)\s+trabalhar)"
    r"\s+(?:in|within|no|na|nos|nas|em|para(?:\s+[oa]s?)?)\s+(?:the\s+)?"
    r"(?:"
    r"(?P<us>united\s+states|u\.s\.a?|usa|eua|\bUS\b|\bus\b)"
    r"|"
    r"(?P<br>brasil|brazil)"
    r")"
    r"(?!\s+(?:office|team|customer|organization|branch|company|client|firm|unit|site|escrit[oó]rio|time|equipe|empresa)\b)"
    r"\b",
)
_RE_COORD_BOUND_JURISDICTION = re.compile(
    r"(?i)\s*(?:,\s*)?(?:or|and|ou|e)\s+"
    r"(?:(?:in|within|no|na|nos|nas|em|para(?:\s+[oa]s?)?)\s+)?(?:the\s+)?"
    r"(?:"
    r"(?P<us>united\s+states|u\.s\.a?|usa|eua|\bUS\b|\bus\b)"
    r"|"
    r"(?P<br>brasil|brazil)"
    r")"
    r"(?!\s+(?:office|team|customer|organization|branch|company|client|firm|unit|site|escrit[oó]rio|time|equipe|empresa)\b)"
    r"\b",
)
_RE_SPONSOR_NO = re.compile(
    r"(?i)\b(n[aã]o oferecemos visto|n[aã]o patrocinamos visto|no visa sponsorship|"
    r"we do not sponsor visas|sponsorship not available|sem patroc[ií]nio de visto|"
    r"unable to sponsor visas?)\b",
)
_RE_SPONSOR_YES = re.compile(
    r"(?i)\b(visa sponsorship available|oferecemos suporte para visto|"
    r"patrocinamos visto|visto de trabalho oferecido|sponsorship available)\b",
)

_RE_CURRENCY_BRL = re.compile(
    r"(?i)(?:R\$\s*|BRL\s*)(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+)"
    r"(?:\s*(?:-|a|at[eé]|to)\s*(?:R\$\s*|BRL\s*)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+))?"
)
_RE_CURRENCY_USD = re.compile(
    r"(?i)(?:US\$\s*|USD\s*)(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+)"
    r"(?:\s*(?:-|to|a)\s*(?:US\$\s*|USD\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+))?"
)
_RE_CURRENCY_EUR = re.compile(
    r"(?i)(?:€\s*|EUR\s*)(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+)"
    r"(?:\s*(?:-|a|at[eé]|to)\s*(?:€\s*|EUR\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+))?"
)
_RE_ISOLATED_DOLLAR = re.compile(r"(?<![A-Za-z0-9])\$(?!\w)")


def parse_money_to_minor_units(raw: str, *, decimal_sep: str) -> int | None:
    """Parses a monetary string to integer minor units (cents) with ZERO float arithmetic.

    decimal_sep: ',' for BRL/EUR, '.' for USD.
    Guarantees exact conversion:
      '0,29' -> 29
      '8.000,29' -> 800029
      '8,000.29' -> 800029
      '5000' -> 500000
    """
    raw = raw.strip()
    if not raw:
        return None

    thousand_sep = "." if decimal_sep == "," else ","
    clean = raw.replace(thousand_sep, "")

    if decimal_sep in clean:
        parts = clean.split(decimal_sep)
        if len(parts) != 2:
            return None
        major_str, frac_str = parts[0], parts[1]
        if not major_str.isdigit() or not frac_str.isdigit():
            return None
        if len(frac_str) > 2:
            return None
        major = int(major_str)
        frac = int(frac_str.ljust(2, "0"))
        return major * 100 + frac
    else:
        if not clean.isdigit():
            return None
        return int(clean) * 100


# -- Derived Job Match Signals Extraction --


def _extract_skills_from_text(text: str) -> set[str]:
    """Tokenizes text and collects all matching TECH_TAXONOMY_V1 tokens."""
    words = re.findall(r"[A-Za-z0-9#+.-]+", text)
    skills: set[str] = set()
    for w in words:
        clean = w.strip(".,;:()")
        canonical = canonicalize_skill(clean)
        if canonical:
            skills.add(canonical)
    # Also check multi-word tokens
    lower_text = text.lower()
    for alias, canonical in TECH_ALIAS_MAP_V1.items():
        if " " in alias and alias in lower_text:
            skills.add(canonical)
    return skills


def derive_job_match_signals(job: Job, policy_version: str) -> DerivedJobMatchSignals:
    """Pure, deterministic extraction of matching signals from canonical Job.

    Treats description_cleaned strictly as untrusted text. Zero LLM, zero network.
    """
    description = job.description_cleaned or ""
    lines = description.splitlines()

    req_skills: set[str] = set()
    pref_skills: set[str] = set()
    unclass_skills: set[str] = set()

    current_section = "unclassified"
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        m_req = _RE_REQUIRED_HEADINGS.match(line_stripped)
        if m_req:
            current_section = "required"
            remainder = m_req.group(1).strip()
            if remainder:
                req_skills.update(_extract_skills_from_text(remainder))
            continue
        m_pref = _RE_PREFERRED_HEADINGS.match(line_stripped)
        if m_pref:
            current_section = "preferred"
            remainder = m_pref.group(1).strip()
            if remainder:
                pref_skills.update(_extract_skills_from_text(remainder))
            continue
        m_other = _RE_OTHER_HEADINGS.match(line_stripped)
        if m_other:
            current_section = "other"
            continue

        extracted = _extract_skills_from_text(line_stripped)
        if current_section == "required":
            req_skills.update(extracted)
        elif current_section == "preferred":
            pref_skills.update(extracted)
        elif current_section in ("unclassified", "other"):
            unclass_skills.update(extracted)

    # Preferred skills should not contain required skills
    pref_skills.difference_update(req_skills)
    unclass_skills.difference_update(req_skills)
    unclass_skills.difference_update(pref_skills)

    # Seniority extraction (title takes precedence, then description)
    title_norm = job.title_normalized or ""
    seniority: JobSeniorityLevel | None = None
    if _RE_SENIORITY_STAFF.search(title_norm):
        seniority = JobSeniorityLevel.STAFF_PRINCIPAL
    elif _RE_SENIORITY_LEAD.search(title_norm):
        seniority = JobSeniorityLevel.LEAD
    elif _RE_SENIORITY_SENIOR.search(title_norm):
        seniority = JobSeniorityLevel.SENIOR
    elif _RE_SENIORITY_MID.search(title_norm):
        seniority = JobSeniorityLevel.MID
    elif _RE_SENIORITY_JUNIOR.search(title_norm):
        seniority = JobSeniorityLevel.JUNIOR
    elif _RE_SENIORITY_INTERN.search(title_norm):
        seniority = JobSeniorityLevel.INTERN
    else:
        # Fallback to description
        if _RE_SENIORITY_STAFF.search(description):
            seniority = JobSeniorityLevel.STAFF_PRINCIPAL
        elif _RE_SENIORITY_LEAD.search(description):
            seniority = JobSeniorityLevel.LEAD
        elif _RE_SENIORITY_SENIOR.search(description):
            seniority = JobSeniorityLevel.SENIOR
        elif _RE_SENIORITY_MID.search(description):
            seniority = JobSeniorityLevel.MID
        elif _RE_SENIORITY_JUNIOR.search(description):
            seniority = JobSeniorityLevel.JUNIOR
        elif _RE_SENIORITY_INTERN.search(description):
            seniority = JobSeniorityLevel.INTERN

    # Salary extraction
    salary_signal: JobSalarySignal | None = None
    # Check for ambiguous isolated $
    has_isolated_dollar = bool(_RE_ISOLATED_DOLLAR.search(description)) and not bool(
        _RE_CURRENCY_USD.search(description)
    )

    if not has_isolated_dollar:
        brl_matches = list(_RE_CURRENCY_BRL.finditer(description))
        usd_matches = list(_RE_CURRENCY_USD.finditer(description))
        eur_matches = list(_RE_CURRENCY_EUR.finditer(description))

        detected_currencies: set[str] = set()
        if brl_matches:
            detected_currencies.add("BRL")
        if usd_matches:
            detected_currencies.add("USD")
        if eur_matches:
            detected_currencies.add("EUR")

        # Multi-currency conflict: exactly 1 distinct currency required; else fails closed
        if len(detected_currencies) == 1:
            currency = next(iter(detected_currencies))
            amounts: list[int] = []
            source_span = ""

            if currency == "BRL":
                for m in brl_matches:
                    v1 = parse_money_to_minor_units(m.group(1), decimal_sep=",")
                    if v1 is not None:
                        amounts.append(v1)
                    if m.group(2):
                        v2 = parse_money_to_minor_units(m.group(2), decimal_sep=",")
                        if v2 is not None:
                            amounts.append(v2)
                    source_span = m.group(0)
            elif currency == "USD":
                for m in usd_matches:
                    v1 = parse_money_to_minor_units(m.group(1), decimal_sep=".")
                    if v1 is not None:
                        amounts.append(v1)
                    if m.group(2):
                        v2 = parse_money_to_minor_units(m.group(2), decimal_sep=".")
                        if v2 is not None:
                            amounts.append(v2)
                    source_span = m.group(0)
            elif currency == "EUR":
                for m in eur_matches:
                    v1 = parse_money_to_minor_units(m.group(1), decimal_sep=",")
                    if v1 is not None:
                        amounts.append(v1)
                    if m.group(2):
                        v2 = parse_money_to_minor_units(m.group(2), decimal_sep=",")
                        if v2 is not None:
                            amounts.append(v2)
                    source_span = m.group(0)

            if amounts:
                # Check period in description with conflicting period detection
                desc_lower = description.lower()
                detected_periods: set[SalaryPeriod] = set()
                for key, p in JOB_SALARY_PERIOD_MAP.items():
                    if key in desc_lower:
                        detected_periods.add(p)

                period: SalaryPeriod | None = (
                    next(iter(detected_periods)) if len(detected_periods) == 1 else None
                )

                min_amt = min(amounts)
                max_amt = max(amounts)
                salary_signal = JobSalarySignal(
                    min_minor=min_amt,
                    max_minor=max_amt,
                    currency=currency,
                    period=period,
                    source_span=source_span,
                )

    # Work Authorization extraction
    work_auth_signal: JobWorkAuthSignal | None = None
    req_auth_match = _RE_AUTH_REQ.search(description)
    sponsor_no_match = _RE_SPONSOR_NO.search(description)
    sponsor_yes_match = _RE_SPONSOR_YES.search(description)

    if req_auth_match or sponsor_no_match or sponsor_yes_match:
        source_span = (
            req_auth_match.group(0)
            if req_auth_match
            else (sponsor_no_match.group(0) if sponsor_no_match else sponsor_yes_match.group(0))  # type: ignore
        )
        sponsorship: bool | None = None
        if sponsor_no_match:
            sponsorship = False
        elif sponsor_yes_match:
            sponsorship = True

        # Extract explicit jurisdiction bound directly to the legal requirement
        detected_jurisdictions: set[str] = set()
        for m in _RE_AUTH_BOUND_JURISDICTION.finditer(description):
            if m.group("us"):
                detected_jurisdictions.add("US")
            if m.group("br"):
                detected_jurisdictions.add("BR")

            # Check if there is an immediately coordinated second jurisdiction in the same clause
            post = description[m.end() :]
            first_delims = [post.find(d) for d in (".", "\n", ";", "!") if post.find(d) != -1]
            clause_len = min(first_delims) if first_delims else min(len(post), 80)
            coord_match = _RE_COORD_BOUND_JURISDICTION.match(post[:clause_len])
            if coord_match:
                if coord_match.group("us"):
                    detected_jurisdictions.add("US")
                if coord_match.group("br"):
                    detected_jurisdictions.add("BR")

        jurisdiction: str | None = None
        if len(detected_jurisdictions) == 1:
            jurisdiction = next(iter(detected_jurisdictions))

        work_auth_signal = JobWorkAuthSignal(
            requires_existing_authorization=bool(req_auth_match),
            sponsorship_offered=sponsorship,
            jurisdiction=jurisdiction,
            source_span=source_span,
        )

    unknown_signals: list[str] = []
    if not req_skills and not pref_skills and not unclass_skills:
        unknown_signals.append("skills_not_detected")
    if seniority is None:
        unknown_signals.append("seniority_not_detected")
    if salary_signal is None:
        unknown_signals.append("salary_not_stated")

    # Limit total extracted skills to 50 for fail-closed DoS protection
    sorted_req = tuple(sorted(req_skills))[:50]
    remaining = 50 - len(sorted_req)
    sorted_pref = tuple(sorted(pref_skills))[:remaining]
    remaining -= len(sorted_pref)
    sorted_unclass = tuple(sorted(unclass_skills))[:remaining]

    # Domain tokens extraction
    words = re.findall(r"[A-Za-z0-9#+.-]+", job.description_cleaned or "")
    extracted_domain_tokens: set[str] = set()
    for w in words:
        dom = canonicalize_domain_token(w)
        if dom and dom in DOMAIN_TAXONOMY_V1:
            extracted_domain_tokens.add(dom)
    sorted_domain_tokens = tuple(sorted(extracted_domain_tokens))

    return DerivedJobMatchSignals(
        required_skills=sorted_req,
        preferred_skills=sorted_pref,
        unclassified_skills=sorted_unclass,
        seniority_signal=seniority,
        salary_requirement=salary_signal,
        work_authorization_requirement=work_auth_signal,
        unknown_signals=tuple(unknown_signals),
        domain_tokens=sorted_domain_tokens,
    )


# -- Candidate Match Signal Reduction --


def derive_candidate_match_signals(
    snapshot: TruthSnapshot,
    policy_version: str,
    evaluation_as_of_date: date,
) -> CandidateMatchSignals:
    """Pure, deterministic reduction of confirmed candidate facts from TruthSnapshot.

    Fact-order independent, ongoing experience duration frozen by evaluation_as_of_date.
    """
    type_adapter: TypeAdapter[FactValue] = TypeAdapter(FactValue)

    # 1. Skills
    skill_map: dict[str, set[uuid.UUID]] = {}
    # 2. Experiences
    experience_list: list[CandidateExperienceSignal] = []
    calendar_months: set[tuple[int, int]] = set()
    # 3. Domain tokens
    domain_map: dict[str, set[uuid.UUID]] = {}
    # 5. Salary preferences
    salary_facts: list[CandidateSalaryPreference] = []
    # 6. Workplace preferences
    acceptable_workplaces: set[JobWorkplaceType] = set()
    workplace_fact_ids: set[uuid.UUID] = set()
    # 7. Location preferences
    location_facts: list[CandidateLocationPreference] = []
    # 8. Work authorizations
    work_auth_map: dict[tuple[str, str], CandidateWorkAuthorizationSignal] = {}
    work_auth_conflicts: set[tuple[str, str]] = set()

    for fact in snapshot.facts:
        try:
            val = type_adapter.validate_python(fact.value_json)
        except Exception as exc:
            logger.debug("Skipping unparseable fact: %s", exc)
            continue

        if isinstance(val, SkillFactValue):
            canonical = canonicalize_skill(val.name)
            if canonical:
                skill_map.setdefault(canonical, set()).add(fact.id)

        elif isinstance(val, ExperienceFactValue):
            exp_months = 0
            if val.start.month is not None:
                start_year = val.start.year
                start_month = val.start.month
                if val.current:
                    end_year = evaluation_as_of_date.year
                    end_month = evaluation_as_of_date.month
                elif val.end is not None and val.end.month is not None:
                    end_year = val.end.year
                    end_month = val.end.month
                else:
                    end_year = None
                    end_month = None

                if end_year is not None and end_month is not None:
                    # Collect calendar months
                    y = start_year
                    m = start_month
                    while (y < end_year) or (y == end_year and m <= end_month):
                        calendar_months.add((y, m))
                        exp_months += 1
                        m += 1
                        if m > 12:
                            m = 1
                            y += 1

            experience_list.append(
                CandidateExperienceSignal(
                    title=val.title,
                    title_normalized=normalize_text_token(val.title),
                    company_name=val.employer,
                    duration_months=exp_months,
                    fact_id=fact.id,
                )
            )

            # Domain tokens from Experience
            exp_text = f"{val.title} {val.description or ''} {' '.join(val.achievements)}"
            words = re.findall(r"[A-Za-z0-9#+.-]+", exp_text)
            for w in words:
                dom = canonicalize_domain_token(w)
                if dom:
                    domain_map.setdefault(dom, set()).add(fact.id)

        elif isinstance(val, ProjectFactValue):
            proj_text = (
                f"{val.name} {val.role or ''} {val.description or ''} {' '.join(val.technologies)}"
            )
            words = re.findall(r"[A-Za-z0-9#+.-]+", proj_text)
            for w in words:
                dom = canonicalize_domain_token(w)
                if dom:
                    domain_map.setdefault(dom, set()).add(fact.id)

        elif isinstance(val, PreferenceFactValue):
            pref = val.preference
            if isinstance(pref, WorkplaceTypePreferenceValue):
                if pref.workplace_types:
                    workplace_fact_ids.add(fact.id)
                    for wpt in pref.workplace_types:
                        if wpt == WorkplaceType.REMOTE:
                            acceptable_workplaces.add(JobWorkplaceType.REMOTE)
                        elif wpt == WorkplaceType.HYBRID:
                            acceptable_workplaces.add(JobWorkplaceType.HYBRID)
                        elif wpt == WorkplaceType.ONSITE:
                            acceptable_workplaces.add(JobWorkplaceType.ON_SITE)

            elif isinstance(pref, LocationPreferenceValue):
                if pref.workplace_types:
                    workplace_fact_ids.add(fact.id)
                    for wpt in pref.workplace_types:
                        if wpt == WorkplaceType.REMOTE:
                            acceptable_workplaces.add(JobWorkplaceType.REMOTE)
                        elif wpt == WorkplaceType.HYBRID:
                            acceptable_workplaces.add(JobWorkplaceType.HYBRID)
                        elif wpt == WorkplaceType.ONSITE:
                            acceptable_workplaces.add(JobWorkplaceType.ON_SITE)

                if pref.country or pref.region or pref.city:
                    location_facts.append(
                        CandidateLocationPreference(
                            country=normalize_text_token(pref.country) if pref.country else None,
                            region=normalize_text_token(pref.region) if pref.region else None,
                            city=normalize_text_token(pref.city) if pref.city else None,
                            fact_ids=(fact.id,),
                        )
                    )

            elif isinstance(pref, SalaryPreferenceValue):
                salary_facts.append(
                    CandidateSalaryPreference(
                        minor_unit_min=pref.minor_unit_min,
                        minor_unit_max=pref.minor_unit_max,
                        currency=pref.currency.upper(),
                        period=pref.period,
                        fact_ids=(fact.id,),
                    )
                )

        elif isinstance(val, WorkAuthorizationFactValue):
            jurisdiction = val.jurisdiction.upper()
            q_key = val.question_key.lower()
            ans_clean = normalize_text_token(val.answer)
            key_map = WORK_AUTHORIZATION_ANSWER_MAP.get(q_key, {})
            semantic_val = key_map.get(ans_clean, WorkAuthSemanticValue.UNKNOWN)

            group_key = (jurisdiction, q_key)
            if group_key in work_auth_map:
                prev_sig = work_auth_map[group_key]
                all_fids = tuple(sorted(set((*prev_sig.fact_ids, fact.id))))
                if prev_sig.semantic_value != semantic_val:
                    work_auth_conflicts.add(group_key)
                    work_auth_map[group_key] = CandidateWorkAuthorizationSignal(
                        jurisdiction=jurisdiction,
                        question_key=q_key,
                        semantic_value=WorkAuthSemanticValue.UNKNOWN,
                        fact_ids=all_fids,
                    )
                else:
                    work_auth_map[group_key] = CandidateWorkAuthorizationSignal(
                        jurisdiction=jurisdiction,
                        question_key=q_key,
                        semantic_value=semantic_val,
                        fact_ids=all_fids,
                    )
            else:
                work_auth_map[group_key] = CandidateWorkAuthorizationSignal(
                    jurisdiction=jurisdiction,
                    question_key=q_key,
                    semantic_value=semantic_val,
                    fact_ids=(fact.id,),
                )

    # Resolve work auth conflicts
    for group_key in work_auth_conflicts:
        prev_sig = work_auth_map[group_key]
        work_auth_map[group_key] = CandidateWorkAuthorizationSignal(
            jurisdiction=prev_sig.jurisdiction,
            question_key=prev_sig.question_key,
            semantic_value=WorkAuthSemanticValue.UNKNOWN,
            fact_ids=prev_sig.fact_ids,
        )

    # Total experience duration
    total_experience_months = len(calendar_months)

    # Sort experiences deterministically: descending duration / title / fact_id
    sorted_experiences = tuple(
        sorted(
            experience_list,
            key=lambda e: (-e.duration_months, e.title_normalized, str(e.fact_id)),
        )
    )

    # Derived Seniority
    derived_seniority: CandidateSeniorityLevel
    if not sorted_experiences:
        derived_seniority = CandidateSeniorityLevel.UNKNOWN
    else:
        all_titles = " ".join(e.title_normalized for e in sorted_experiences)
        if total_experience_months < 12:
            if re.search(
                r"\b(j[uú]nior|junior|jr|analyst|developer|engenheiro|engineer)\b", all_titles
            ):
                derived_seniority = CandidateSeniorityLevel.JUNIOR
            else:
                derived_seniority = CandidateSeniorityLevel.INTERN
        elif total_experience_months < 36:
            derived_seniority = CandidateSeniorityLevel.JUNIOR
        elif total_experience_months < 72:
            derived_seniority = CandidateSeniorityLevel.MID
        elif total_experience_months < 120:
            derived_seniority = CandidateSeniorityLevel.SENIOR
        else:
            if re.search(r"\b(staff|principal|architect|arquiteto)\b", all_titles):
                derived_seniority = CandidateSeniorityLevel.STAFF_PRINCIPAL
            else:
                derived_seniority = CandidateSeniorityLevel.LEAD

    # Skills tuple
    candidate_skills = tuple(
        CandidateSkillSignal(
            canonical_skill=k,
            fact_ids=tuple(sorted(fids)),
        )
        for k, fids in sorted(skill_map.items())
    )

    # Domain tokens tuple
    candidate_domain_tokens = tuple(
        CandidateDomainTokenSignal(
            canonical_token=k,
            fact_ids=tuple(sorted(fids)),
        )
        for k, fids in sorted(domain_map.items())
    )

    # Target roles
    target_roles = tuple(
        sorted(
            {
                normalize_text_token(r)
                for r in (snapshot.target_roles or [])
                if normalize_text_token(r)
            }
        )
    )

    # Salary preference collapse
    salary_pref: CandidateSalaryPreference | None = None
    salary_ambiguous = False
    if len(salary_facts) == 1:
        salary_pref = salary_facts[0]
    elif len(salary_facts) > 1:
        # Check if identical
        first = salary_facts[0]
        if all(
            s.currency == first.currency
            and s.period == first.period
            and s.minor_unit_min == first.minor_unit_min
            and s.minor_unit_max == first.minor_unit_max
            for s in salary_facts
        ):
            all_sal_fids = tuple(sorted(set(fid for s in salary_facts for fid in s.fact_ids)))
            salary_pref = CandidateSalaryPreference(
                minor_unit_min=first.minor_unit_min,
                minor_unit_max=first.minor_unit_max,
                currency=first.currency,
                period=first.period,
                fact_ids=all_sal_fids,
            )
        else:
            salary_ambiguous = True

    # Location preferences sorted deterministically by total_sort_key
    unique_locations: dict[tuple[str, str, str], CandidateLocationPreference] = {}
    for loc in location_facts:
        key = loc.total_sort_key
        if key not in unique_locations:
            unique_locations[key] = loc
        else:
            combined_fids = tuple(sorted(set(unique_locations[key].fact_ids + loc.fact_ids)))
            unique_locations[key] = CandidateLocationPreference(
                country=loc.country,
                region=loc.region,
                city=loc.city,
                fact_ids=combined_fids,
            )

    sorted_locations = tuple(sorted(unique_locations.values(), key=lambda loc: loc.total_sort_key))

    return CandidateMatchSignals(
        skills=candidate_skills,
        skill_names=frozenset(skill_map.keys()),
        experiences=sorted_experiences,
        total_experience_months=total_experience_months,
        domain_tokens=candidate_domain_tokens,
        domain_token_names=frozenset(domain_map.keys()),
        target_roles=target_roles,
        derived_seniority=derived_seniority,
        salary_preference=salary_pref,
        salary_ambiguous=salary_ambiguous,
        acceptable_workplace_types=frozenset(acceptable_workplaces),
        acceptable_locations=sorted_locations,
        work_authorizations=work_auth_map,
        confirmed_fact_count=len(snapshot.facts),
        workplace_preference_fact_ids=tuple(sorted(workplace_fact_ids)),
    )
