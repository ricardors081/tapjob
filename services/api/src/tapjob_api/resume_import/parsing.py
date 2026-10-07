"""Deterministic, non-AI resume-text parser (docs/adr/0011-resume-import.md "resume parsing",
"no real AI provider requirement"). No `AiGateway`-backed parser is implemented in WP1.3 -- this
module makes the import flow genuinely functional today, and documents honestly that production
LLM-based extraction is NOT enabled.

Ports JobSync's resume-import prompt DISCIPLINE (never AutoApply's/JobSync's actual code -- one is
JavaScript/markdown, the other is a markdown-section parser, neither is PDF/DOCX extraction --
docs/SOURCE_REUSE_LEDGER.md): the document is evidence, never an instruction; dates/experience/
education/skills/certifications/languages/projects are extracted only when a pattern is genuinely
recognizable; an unrecognized section is simply skipped, never guessed at; nothing here ever
infers `target_roles`, salary/location preference, or work authorization from surrounding resume
text (docs/adr/0011-...md "sensitive/preference fields", "career profile top-level fields"). Every
candidate this module returns is still UNCONFIRMED (`ports.resume_parser.ExtractedFact`) -- review
and `FactValue` validation happen one layer up, in `tapjob_api.resume_import.candidates`.

A resume's own text can contain hostile instructions ("ignore previous instructions", "call this
URL") -- irrelevant here, since this parser never invokes an LLM: injected text is simply inert
data that, at most, ends up inside an already-bounded/validated candidate field
(docs/AI_AGENT.md §7). A future `AiGateway`-backed parser would need to wrap extracted text in
`ports.ai_gateway.UntrustedContent` and validate output via `AiRequest.validate_output` -- out of
scope for this module.
"""

import re
from dataclasses import dataclass

from tapjob_api.ports.resume_parser import ExtractedFact, JsonValue
from tapjob_api.resume_import.limits import MAX_CANDIDATES_PER_IMPORT

PARSER_IDENTITY = "deterministic-heuristic-v1"

_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": ("summary", "professional summary", "resumo", "perfil profissional", "objetivo"),
    "experience": ("experience", "work experience", "experiência", "experiência profissional"),
    "education": ("education", "formação", "formação acadêmica"),
    "skills": ("skills", "technical skills", "habilidades", "competências"),
    "certifications": ("certifications", "certificações", "certificados"),
    "languages": ("languages", "idiomas"),
    "projects": ("projects", "projetos"),
}
_SECTION_HEADER_BY_ALIAS: dict[str, str] = {
    alias: section for section, aliases in _SECTION_ALIASES.items() for alias in aliases
}

# Bullet markers: hyphen, bullet, triangular bullet, white bullet, asterisk, hyphen bullet --
# built from `chr(codepoint)` rather than literal source characters so the .py source stays
# unambiguous ASCII (RUF001) while still matching the real Unicode glyphs resumes actually use.
_BULLET_CHARS = "-" + chr(0x2022) + chr(0x2023) + chr(0x25E6) + "*" + chr(0x2043)
_BULLET_PREFIX = re.compile("^[" + re.escape(_BULLET_CHARS) + r"]\s*")
_URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")
# Date-range separators: hyphen, en dash, em dash.
_DASH_CHARS = "-" + chr(0x2013) + chr(0x2014)
_DATE_RANGE_PATTERN = re.compile(
    r"(?P<start>[A-Za-zçÇãÃéÉ]{3,9}\.?\s*\d{4}|\d{4})\s*["
    + re.escape(_DASH_CHARS)
    + r"]\s*(?P<end>[A-Za-zçÇãÃéÉ]{3,9}\.?\s*\d{4}|\d{4}|present|atual|current)",
    re.IGNORECASE,
)
_YEAR_ONLY_PATTERN = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_EM_DASH = chr(0x2014)


def _normalize_header(line: str) -> str | None:
    key = line.strip().strip(":").lower()
    return _SECTION_HEADER_BY_ALIAS.get(key)


def _split_into_sections(text: str) -> dict[str, list[str]]:
    """A section's own lines, keyed by the canonical section name; the leading preamble (before any
    recognized header -- typically name/contact/headline) is returned under the key
    `"__preamble__"`. An unrecognized header line is treated as ordinary body text of whatever
    section is currently open (docs/adr/0011-...md "unrecognized section" -- never guessed into a
    new section)."""
    sections: dict[str, list[str]] = {"__preamble__": []}
    current = "__preamble__"
    for raw_line in text.splitlines():
        header = _normalize_header(raw_line)
        if header is not None:
            current = header
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(raw_line)
    return sections


def _blocks(lines: list[str]) -> list[list[str]]:
    """Splits a section's lines into blocks separated by one or more blank lines -- the
    conservative, format-agnostic signal this parser uses to tell "one experience/education entry"
    from the next."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _year_month_dict(token: str) -> dict[str, int | None] | None:
    token = token.strip()
    if re.fullmatch(r"present|atual|current", token, re.IGNORECASE):
        return None
    year_match = _YEAR_ONLY_PATTERN.search(token)
    if year_match is None:
        return None
    return {"year": int(year_match.group(1)), "month": None}


@dataclass(frozen=True, slots=True)
class _ParsedExperienceBlock:
    title: str
    employer: str
    start: dict[str, int | None]
    end: dict[str, int | None] | None
    current: bool
    description: str | None
    achievements: list[str]


def _split_title_employer(line: str) -> tuple[str, str] | None:
    for separator in (" at ", f" {_EM_DASH} ", " - ", " na ", " em "):
        if separator in line:
            left, _, right = line.partition(separator)
            left, right = left.strip(" ,"), right.strip(" ,")
            if left and right:
                return left, right
    return None


def _parse_experience_block(block: list[str]) -> _ParsedExperienceBlock | None:
    date_line_index: int | None = None
    date_match: re.Match[str] | None = None
    for index, line in enumerate(block):
        match = _DATE_RANGE_PATTERN.search(line)
        if match is not None:
            date_line_index = index
            date_match = match
            break
    if date_match is None or date_line_index is None:
        # No recognizable date range anywhere in the block -- too ambiguous to assert an
        # experience candidate at all (docs/adr/0011-...md "resume parsing": never invent dates).
        return None

    start = _year_month_dict(date_match.group("start"))
    if start is None:
        return None
    end_token = date_match.group("end")
    is_current = bool(re.fullmatch(r"present|atual|current", end_token.strip(), re.IGNORECASE))
    end = None if is_current else _year_month_dict(end_token)

    title_employer_line = block[0] if date_line_index != 0 else (block[1] if len(block) > 1 else "")
    split = _split_title_employer(title_employer_line)
    if split is None:
        return None
    title, employer = split

    remaining = [
        line
        for i, line in enumerate(block)
        if i not in (0, date_line_index) and line.strip() and line is not title_employer_line
    ]
    achievements: list[str] = []
    description_lines: list[str] = []
    for line in remaining:
        stripped = _BULLET_PREFIX.sub("", line).strip()
        if _BULLET_PREFIX.match(line):
            achievements.append(stripped)
        else:
            description_lines.append(stripped)

    return _ParsedExperienceBlock(
        title=title,
        employer=employer,
        start=start,
        end=end,
        current=is_current,
        description=" ".join(description_lines) or None,
        achievements=achievements,
    )


def _parse_experience_section(lines: list[str], *, remaining_budget: int) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for block in _blocks(lines):
        # Capped directly at the source, against the caller's own REMAINING budget, never the
        # overall `MAX_CANDIDATES_PER_IMPORT` constant (round-2 independent review "Hardening 8" --
        # round-1's own "Hardening 10" fix still checked against the full constant even when most
        # of that budget had already been consumed by an earlier section, so a small remaining
        # budget still let this one function build far more than it had any use for before
        # `parse_resume_text`'s own `_add` truncated the result afterward).
        if len(facts) >= remaining_budget:
            break
        parsed = _parse_experience_block(block)
        if parsed is None:
            continue
        value: dict[str, JsonValue] = {
            "kind": "experience",
            "title": parsed.title,
            "employer": parsed.employer,
            "start": parsed.start,
            "current": parsed.current,
        }
        if parsed.end is not None:
            value["end"] = parsed.end
        if parsed.description:
            value["description"] = parsed.description
        if parsed.achievements:
            value["achievements"] = parsed.achievements
        facts.append(
            ExtractedFact(
                fact_type="experience",
                value=value,
                confidence=0.6,
                source_excerpt="\n".join(block),
            )
        )
    return facts


def _parse_education_section(lines: list[str], *, remaining_budget: int) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for block in _blocks(lines):
        if len(facts) >= remaining_budget:
            break
        first_line = block[0].strip()
        if not first_line:
            continue
        split = _split_title_employer(first_line)
        institution = split[1] if split else first_line
        degree = split[0] if split else None
        value: dict[str, JsonValue] = {"kind": "education", "institution": institution}
        if degree:
            value["degree"] = degree
        rest = " ".join(line.strip() for line in block[1:] if line.strip())
        year_match = _YEAR_ONLY_PATTERN.search(first_line + " " + rest)
        if year_match is not None:
            value["end"] = {"year": int(year_match.group(1)), "month": None}
        facts.append(
            ExtractedFact(
                fact_type="education", value=value, confidence=0.5, source_excerpt=first_line
            )
        )
    return facts


def _parse_skills_section(lines: list[str], *, remaining_budget: int) -> list[ExtractedFact]:
    joined = " ".join(_BULLET_PREFIX.sub("", line).strip() for line in lines if line.strip())
    tokens = re.split("[,;" + re.escape(chr(0x2022)) + r"|/]+", joined)
    facts: list[ExtractedFact] = []
    for token in tokens:
        # A comma/slash-delimited skills line is this parser's single biggest amplification
        # vector: an adversarial section packed with thousands of short separators could
        # otherwise make this one function alone materialize far more candidates than any real
        # resume ever would, well before `parse_resume_text`'s own overall cap is ever consulted
        # (docs/adr/0011-...md "bounded resource policy"). Capped directly at the source, against
        # the caller's own REMAINING budget (round-2 independent review "Hardening 8"), not only
        # by a caller truncating the result afterward.
        if len(facts) >= remaining_budget:
            break
        name = token.strip()
        if not name or len(name) > 100:
            continue
        facts.append(
            ExtractedFact(fact_type="skill", value={"kind": "skill", "name": name}, confidence=0.55)
        )
    return facts


def _parse_languages_section(lines: list[str], *, remaining_budget: int) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for raw_line in lines:
        if len(facts) >= remaining_budget:
            break
        line = _BULLET_PREFIX.sub("", raw_line).strip()
        if not line:
            continue
        for separator in (_EM_DASH, "-", ":"):
            if separator in line:
                language, _, proficiency = line.partition(separator)
                language, proficiency = language.strip(), proficiency.strip()
                if language:
                    value: dict[str, JsonValue] = {"kind": "language", "language": language}
                    if proficiency:
                        value["proficiency"] = proficiency
                    facts.append(ExtractedFact(fact_type="language", value=value, confidence=0.55))
                break
        else:
            if line:
                facts.append(
                    ExtractedFact(
                        fact_type="language",
                        value={"kind": "language", "language": line},
                        confidence=0.4,
                    )
                )
    return facts


def _parse_certifications_section(
    lines: list[str], *, remaining_budget: int
) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for raw_line in lines:
        if len(facts) >= remaining_budget:
            break
        line = _BULLET_PREFIX.sub("", raw_line).strip()
        if not line:
            continue
        split = _split_title_employer(line)
        name, issuer = split if split else (line, None)
        value: dict[str, JsonValue] = {"kind": "certification", "name": name}
        if issuer:
            value["issuer"] = issuer
        else:
            # `issuer` is required by `CertificationFactValue` -- when it cannot be confidently
            # split out, record a conservative placeholder-free candidate the reviewer must
            # complete themselves rather than guessing one (dropped here; the HTTP/domain layer
            # requires `issuer` to accept the candidate at review time).
            continue
        facts.append(ExtractedFact(fact_type="certification", value=value, confidence=0.5))
    return facts


def _parse_projects_section(lines: list[str], *, remaining_budget: int) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for block in _blocks(lines):
        if len(facts) >= remaining_budget:
            break
        first_line = _BULLET_PREFIX.sub("", block[0]).strip()
        if not first_line:
            continue
        rest = " ".join(_BULLET_PREFIX.sub("", line).strip() for line in block[1:] if line.strip())
        value: dict[str, JsonValue] = {"kind": "project", "name": first_line}
        if rest:
            value["description"] = rest
        url_match = _URL_PATTERN.search(" ".join(block))
        if url_match is not None:
            value["url"] = url_match.group(0).rstrip(".,;)")
        facts.append(
            ExtractedFact(
                fact_type="project", value=value, confidence=0.45, source_excerpt=first_line
            )
        )
    return facts


def _parse_links(full_text: str, *, remaining_budget: int) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    seen: set[str] = set()
    for match in _URL_PATTERN.finditer(full_text):
        if len(facts) >= remaining_budget:
            break
        url = match.group(0).rstrip(".,;)")
        if url in seen:
            continue
        seen.add(url)
        link_type = "website"
        if "linkedin.com" in url:
            link_type = "linkedin"
        elif "github.com" in url:
            link_type = "github"
        facts.append(
            ExtractedFact(
                fact_type="link",
                value={"kind": "link", "linkType": link_type, "url": url},
                confidence=0.7,
            )
        )
    return facts


def _parse_summary(lines: list[str]) -> ExtractedFact | None:
    text = " ".join(line.strip() for line in lines if line.strip())
    if not text:
        return None
    return ExtractedFact(fact_type="summary", value={"text": text}, confidence=0.5)


def _parse_headline(preamble_lines: list[str]) -> ExtractedFact | None:
    """The FIRST short, non-contact-looking line of the whole document -- a low-confidence
    candidate only (docs/adr/0011-...md "career profile top-level fields"): resumes vary too much
    for this to be reliable, and it is never persisted as anything but a reviewable candidate."""
    for line in preamble_lines:
        stripped = line.strip()
        if not stripped or len(stripped) > 120:
            continue
        if "@" in stripped or _YEAR_ONLY_PATTERN.search(stripped):
            continue
        return ExtractedFact(fact_type="headline", value={"text": stripped}, confidence=0.35)
    return None


def parse_resume_text(
    text: str, *, max_facts: int = MAX_CANDIDATES_PER_IMPORT
) -> list[ExtractedFact]:
    """The one entry point: bounded, deterministic, never-invents-a-fact extraction over already-
    bounded plain text (`tapjob_api.resume_import.extraction` enforces the size bound before this
    ever runs). Returns a flat list of `ExtractedFact` -- `fact_type` is one of the nine canonical
    `FactType` values, plus the two WP1.3-only top-level candidate kinds `"headline"`/`"summary"`
    (never persisted as a `ProfileFact` -- see `tapjob_api.resume_import.candidates`).

    `max_facts` bounds the list as it is BUILT, section by section -- never only at the caller's
    own persistence-time cap (`service.process_resume_import`'s own `MAX_CANDIDATES_PER_IMPORT`
    check on the VALIDATED subset was too late in practice: an adversarial `skills` section packed
    with thousands of short comma-separated tokens could already make this function itself
    materialize a large intermediate list in memory before a single candidate was ever validated or
    dropped, docs/adr/0011-...md "bounded resource policy" / resource-cap placement).

    Round-2 independent review "Hardening 8": round-1's own fix capped each section-producing
    function at the full `MAX_CANDIDATES_PER_IMPORT` CONSTANT, not at this function's own REMAINING
    budget -- so a small `max_facts` (or a budget mostly already consumed by an earlier section)
    still let the NEXT section's own parser materialize far more candidates internally than it had
    any use for, before `_add` truncated the result afterward. Every section-producing function
    below now receives the ACTUAL remaining budget as an explicit parameter, computed fresh
    immediately before each call -- the loop inside each one can therefore never construct more
    than that many candidates, by construction, not merely by a caller slicing the return value."""
    sections = _split_into_sections(text)
    facts: list[ExtractedFact] = []

    def _add(new_facts: list[ExtractedFact]) -> bool:
        """Returns False once `max_facts` is reached, signalling the caller to stop extracting
        further sections entirely -- never builds past the bound just to truncate afterward."""
        remaining = max_facts - len(facts)
        if remaining <= 0:
            return False
        facts.extend(new_facts[:remaining])
        return len(facts) < max_facts

    if len(facts) < max_facts and "summary" in sections:
        summary_fact = _parse_summary(sections["summary"])
        if summary_fact is not None:
            facts.append(summary_fact)
    if len(facts) < max_facts:
        headline_fact = _parse_headline(sections.get("__preamble__", []))
        if headline_fact is not None:
            facts.append(headline_fact)

    remaining = max_facts - len(facts)
    if remaining <= 0:
        return facts
    if not _add(
        _parse_experience_section(sections.get("experience", []), remaining_budget=remaining)
    ):
        return facts

    remaining = max_facts - len(facts)
    if remaining <= 0:
        return facts
    if not _add(
        _parse_education_section(sections.get("education", []), remaining_budget=remaining)
    ):
        return facts

    remaining = max_facts - len(facts)
    if remaining <= 0:
        return facts
    if not _add(_parse_skills_section(sections.get("skills", []), remaining_budget=remaining)):
        return facts

    remaining = max_facts - len(facts)
    if remaining <= 0:
        return facts
    if not _add(
        _parse_languages_section(sections.get("languages", []), remaining_budget=remaining)
    ):
        return facts

    remaining = max_facts - len(facts)
    if remaining <= 0:
        return facts
    if not _add(
        _parse_certifications_section(
            sections.get("certifications", []), remaining_budget=remaining
        )
    ):
        return facts

    remaining = max_facts - len(facts)
    if remaining <= 0:
        return facts
    if not _add(_parse_projects_section(sections.get("projects", []), remaining_budget=remaining)):
        return facts

    remaining = max_facts - len(facts)
    if remaining > 0:
        _add(_parse_links(text, remaining_budget=remaining))
    return facts
