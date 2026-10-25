# ADR-0017 — Career Understanding Engine V1

**Status:** Accepted  
**Date:** 2026-10-25  
**Decision:** D-059 (`docs/DECISIONS.md`)

---

## 1. Context & Problem Statement

Prior to this decision, TapJob's resume intake relied on a deterministic heuristic parser (`services/api/src/tapjob_api/resume_import/parsing.py`, parser identity: `deterministic-heuristic-v1`). While effective for strict single-column layouts with standard Portuguese headings, this parser proved fragile when encountering:
- multi-column or non-standard visual layouts;
- unconventional or omitted section headings;
- achievements and technologies embedded naturally within job narrative descriptions;
- multilingual or hybrid documents;
- publication records, academic research, and complex project portfolios.

A deterministic heuristic parser cannot reliably capture the multifaceted reality of candidates' careers without brittle regex expansions. To deliver TapJob's vision of a personal career platform, the intake pipeline must evolve into a **Career Understanding Engine** capable of semantically extracting facts while rigorously upholding TapJob's core mission: the **Professional Truth Store**.

---

## 2. Core Principles & Non-Negotiable Invariants

### Principle 1: Resume Document != Career Truth
A resume is an unverified, static document submitted by a candidate or third party. It may contain aspirational phrasing, outdated roles, formatting artifacts, or unverified claims. The document itself is **not** career truth.

### Principle 2: Semantic Extraction != Confirmed Fact
Output produced by an AI model or semantic parser constitutes an *unconfirmed candidate extraction* (`ResumeImportCandidate`), not a verified profile fact. No extraction may be directly written to `ProfileFact` without explicit human candidate review and acceptance.

### Principle 3: Suggestion != Factual Evidence
A parser may extract suggested skills, technologies, or titles from descriptive text, but these suggestions do not constitute factual evidence until anchored to source text and confirmed by the candidate.

### Principle 4: Professional Truth Store Remains Canonical
The PostgreSQL table `profile_facts` (`docs/adr/0010-career-profile-truth-store.md`) remains the sole durable source of truth for the candidate's career profile. All downstream AI generations (ATS resume tailoring, application kits, interview preparation) must strictly query confirmed `ProfileFact` records.

### Principle 5: Semantic Parser Primary / Deterministic Fallback
The `ResumeParser` protocol (`ports.resume_parser.ResumeParser`) is retained as the stable architectural boundary.
- **Primary:** `SemanticResumeParser` (`semantic-openai-v1`), utilizing Structured Outputs.
- **Fallback:** `DeterministicResumeParser` (`deterministic-heuristic-v1`), automatically invoked when AI extraction fails (network blips, provider timeouts, schema validation rejections).
- The parser identity is recorded durably on `ResumeImport.parser_identity` for auditability and observability.

### Principle 6: Conceptual Reference vs External Dependency
Open standards such as JSON Resume serve as valuable conceptual references for taxonomy and section naming. However, TapJob does not introduce external JSON Resume packages or tight schema bindings into its domain. Domain schemas (`tapjob_api.career_profile` and `tapjob_api.resume_import`) are natively owned and typed via Pydantic and SQLAlchemy.

---

## 3. Architecture Decisions

### Decision A: Provider-Neutral AI Integration via `AiGateway`
- Following `docs/adr/0005-internal-ports-capability-model.md`, AI generation is mediated by the internal port `ports.ai_gateway.AiGateway`.
- The domain (`resume_import`, `career_profile`, `messaging`) never imports third-party AI provider SDKs (e.g. OpenAI, Anthropic).
- The first adapter implementation is `OpenAiGateway` (`tapjob_api.ai_gateway.openai_gateway.OpenAiGateway`), built purely over `httpx.AsyncClient`.
- The gateway uses OpenAI Structured Outputs (`response_format: {"type": "json_schema", "json_schema": {"strict": true}}`) with Pydantic JSON schemas.

### Decision B: Untrusted Input Defense & Grounding
- Uploaded resumes are treated as untrusted external data (`UntrustedContent(source_kind="resume_document")`).
- Prompts enforce explicit grounding: every extracted item must include a `source_excerpt` substring verifiable against the original resume text. If an excerpt is not present in the document text, the candidate is discarded or scrubbed of fictitious excerpts.
- Strict anti-hallucination rule: candidate name must **never** be copied into professional headline. If no explicit headline is present, the field remains null.
- Extracted skills are deduplicated and normalized.

### Decision C: Canonical Fact Model Expansion — `Publication`
To represent academic papers, conference presentations, journals, and technical publications:
- Added `FactType.PUBLICATION = "publication"` to `career_profile.models`.
- Added `PublicationFactValue` to `career_profile.fact_values` (`title: str`, `publisher: str | None`, `date: str | None`, `url: HttpUrl | None`, `summary: str | None`).
- Added `CandidateKind.PUBLICATION = "publication"` to `resume_import.candidates`.
- Added Alembic migration `20261025_0014_add_publication_fact_type.py` expanding the `profile_fact_type` CHECK constraint.

### Decision D: WhatsApp Profile Query Presentation Decoupling
- In WhatsApp conversational turns (`O que você sabe sobre meu perfil?`), the snapshot renderer was previously echoing raw enums (`language`, `education`, `publication`).
- Implemented `format_profile_query_summary` in `tapjob_api.messaging.profile_formatter`, presenting localized Portuguese headers (`💼 Experiência`, `🎓 Formação`, `🛠️ Competências`, `🌎 Idiomas`, `📚 Certificações / Cursos`, `📝 Publicações`, `🚀 Projetos`), suppressing raw system enums and cleanly organizing candidate facts.

---

## 4. Consequences & Migration Path

- **Positive:**
  - Robust understanding of unstructured, non-standard, and multi-column resumes.
  - Resilience against AI provider degradation through automatic deterministic fallback.
  - Zero leakage of raw database enums to end users on messaging channels.
  - Grounding validation protects against hallucinations.
- **Negative / Operational Requirements:**
  - Semantic extraction requires `OPENAI_API_KEY` configured in runtime settings. When unset, system safely operates on deterministic fallback.
  - Additional latency (~1-3s) during resume parsing task in worker, well within Celery task lease bounds.
