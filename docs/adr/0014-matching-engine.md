# ADR-0014 — Matching Engine Architecture, Scoring & Explainability (WP2.2)

**Status:** Accepted  
**Date:** 2026-10-08  
**Decision:** D-056 (`docs/DECISIONS.md`)  

---

## 1. Context & Scope

WP2.1-A (ADR-0012, D-054) and WP2.1-B (ADR-0013, D-055) delivered the canonical Jobs domain (`Company`, `Job`, `JobOrigin`), manual intake endpoints, deterministic normalization, and external hosted ATS connectors (Greenhouse, Lever, Ashby) with ingestion scheduling. Both are `VERIFIED` on `main`.

WP2.2 is the **Opportunity Matching Engine** (`docs/IMPLEMENTATION_PLAN.md`, `docs/DELIVERY_ORDER.md`). It provides the intelligence core that evaluates compatibility between a candidate's Professional Truth Store (`CareerProfile` / `TruthSnapshot`, WP1.2) and an ingested opportunity (`Job`, WP2.1).

### Minimum Vertical Slice Requirement (WhatsApp-First)
Per `docs/DELIVERY_ORDER.md` §3–4, the Minimum Career Core requires an explainable match before the WhatsApp alpha journey is viable:
- User submits a job (or reviews an ingested job) -> TapJob calculates and explains the match ("vale a pena essa vaga?") -> prepares tailored application kit.
- The matching engine must be **fast (< 50ms p95 single evaluation)**, **100% deterministic**, **grounded in confirmed facts**, and **structured for explainability**.

### Explicit Boundaries & Non-Goals for WP2.2
- **Out of Scope (Deferred to WP2.3 — Job Feed & Alerts):**
  - Public job recommendation feed (`GET /api/v1/jobs`);
  - Asynchronous bulk matching / ranking over thousands of jobs;
  - Candidate job alert subscriptions and push notification triggers.
- **Out of Scope (Deferred to WP3.x / WP4.x):**
  - Resume tailoring and application submission (Apply Engine);
  - WhatsApp webhook transport adapter (MessagingChannel);
  - Mobile UI match screens.
- **Strict Constraint for this Decision Gate:**
  - This ADR defines architecture, contracts, scoring formulas, data model, and evaluation gates.
  - Zero product code, zero migrations, zero functional endpoints, and zero runtime alterations are introduced in this decision gate PR.

---

## 2. Source Reuse Audit

Per `docs/SOURCE_REUSE_BLUEPRINT.md` and `docs/SOURCE_REUSE_LEDGER.md`, four repositories provide reference material for WP2.2:

| Donor Repository | Audited SHA | Capability | Reuse Mode | Rationale & Boundaries |
|---|---|---|---|---|
| `wardoep/jobbot` | `5932b3782412aeda4ed492df7c788b1248121ecb` | Hard-filter gate & pipeline orchestration | **ADAPT** | JobBot provides a clean Python pipeline separating knock-out gates from scoring (`app/matching/gate.py`, `scorer.py`, `engine.py`). ADAPTED to consume TapJob's typed `TruthSnapshot`, `ProfileFact`s, and `JobWorkplaceType`. No untyped dictionaries, external scrapers, or ORM models imported. |
| `AbhishekMandapmalvi/AutoApply` | `053071ba1bba5733b522d78c3d645002d817e55a` | Lexical scoring & skill weighting | **ADAPT** | AutoApply provides keyword overlap and required vs. preferred skill weighting (`core/resume_scorer.py`). ADAPTED into a bounded, deterministic pure-Python lexical scorer operating on confirmed facts vs. normalized job tokens. Scikit-learn pickles and non-deterministic floats are rejected. |
| `career-ops-hq/career-ops` | `d6a6c12db6d579ae0f3e6875d247409f6b8c9806` | Reasoning discipline & evidence grounding | **PORT** | Enforces that every match explanation must point to explicit `ProfileFact.id`s and job text spans (`keyword-match.mjs`). PORTED into TapJob's `MatchExplanation` and `EvidenceRef` structures. Untrusted job text is treated strictly as evidence, never instruction. |
| `Gsync/jobsync` | `ef3d7eec5c9aebaa1c6e03fc3ddd62d7dca8e37f` | Match regression evals & test fixtures | **PORT** | JobSync defines rich job-match eval fixtures (`evals/job-match/*`). PORTED into a deterministic pytest golden test suite (`tests/test_matching_evals.py`) asserting score bands, eligibility, and blocker identification. |

Single canonical runtime owner per capability rule is strictly maintained: `tapjob_api.matching` owns the entire runtime.

---

## 3. Architectural Decisions

### Decision 1: `MatchingEngine` Remains an Internal Domain Module (Not a Port)
- ADR-0005 (§1) decided that `MatchingEngine` is TapJob's sovereign domain logic, not a provider-neutral abstraction. There is no external third-party provider to swap out.
- Extracting a `Protocol` under `tapjob_api.ports` remains unjustified ("theoretical abstraction").
- `MatchingEngine` is implemented as an internal domain service under `tapjob_api.matching` (`service.py`, `engine.py`, `gate.py`, `lexical.py`, `explainability.py`).

### Decision 2: Match Input Versioning — `JOB_MATCH_FINGERPRINT`
In WP2.1, `similarity_fingerprint` was designed strictly for deduplication:
```python
compute_similarity_fingerprint(
    company_id, title_normalized, location_normalized, description_cleaned
)
```
Crucially, `similarity_fingerprint` **does NOT include `workplace_type`**. If an upstream ATS updates `workplace_type` from `remote` to `on_site` (e.g. via a workplace hint change), `similarity_fingerprint` remains unchanged while the matching evaluation materially alters.

**Correction:**
1. We preserve `similarity_fingerprint` for its WP2.1 deduplication role without mutation.
2. We introduce **`JOB_MATCH_FINGERPRINT`**, a dedicated, immutable matching input digest for WP2.2 derived deterministically from all job fields affecting evaluation:
   - `job_id`: UUID
   - `title_normalized`: str
   - `location_normalized`: str | None
   - `workplace_type`: str (`remote`, `hybrid`, `on_site`, `unknown`)
   - `description_cleaned_sha256`: SHA-256 of `description_cleaned`
3. Canonical Serialization and Hash:
   ```python
   def compute_job_match_fingerprint(job: Job) -> str:
       payload = {
           "job_id": str(job.id),
           "title_normalized": job.title_normalized,
           "location_normalized": job.location_normalized,
           "workplace_type": job.workplace_type.value,
           "description_cleaned_sha256": hashlib.sha256(
               job.description_cleaned.encode("utf-8")
           ).hexdigest(),
       }
       canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
       return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
   ```
4. `JOB_MATCH_FINGERPRINT` is stored in `match_results.job_match_fingerprint`, indexed in the database unique constraint, and forms part of the Redis cache key.

### Decision 3: Deterministic Match Core vs. Presentation Layer & Time Handling

To guarantee true mathematical reproducibility, we formally partition the engine into three distinct layers:

#### A. `DeterministicMatchCore`
Contains all business logic outputs whose exact reproducibility is invariant:
- `overall_score`: int (0..100)
- `eligibility`: `MatchEligibility` (`ELIGIBLE`, `INELIGIBLE`, `REVIEW_REQUIRED`)
- `hard_filter_results`: tuple of gate evaluations
- `score_components`: component integer breakdown
- `blockers`: tuple of hard blockers
- `strengths`: tuple of grounded strengths
- `missing_requirements`: tuple of identified requirement gaps
- `critical_unknowns`: tuple of critical unknown signals (`CriticalUnknown`)
- `deterministic_template_explanation`: `MatchExplanation` (standardized template)
- `grounded_fact_ids`: tuple of UUIDs
- `profile_version`: int
- `job_match_fingerprint`: str
- `policy_version`: str
- `evaluation_as_of_date`: date (UTC `YYYY-MM-DD`)
- `deterministic_result_digest`: str (SHA-256 of canonical deterministic payload)

#### B. Operational Metadata (Excluded from Deterministic Equality)
- `id`: UUID PK
- `computed_at`: datetime (live execution timestamp)
- `created_at`: datetime
- `updated_at`: datetime
- `cache_hit`: bool

#### C. Handling Current / Ongoing Experience (`current = True`)
`ExperienceFactValue` allows `current = True` with `end = None`. If elapsed duration were computed against `now()`, the score would drift across days while `profile_version` remains identical.
- **Policy:** The engine accepts an explicit **`evaluation_as_of_date: date`** (defaulting to current UTC date `now(timezone.utc).date()`).
- Duration of ongoing experience is calculated strictly as elapsed months between `start` and `evaluation_as_of_date`.
- Given `(user_id, profile_version, job_match_fingerprint, policy_version, evaluation_as_of_date)`, the computation is 100% frozen and bitwise reproducible. Across UTC day boundaries, a new date produces a distinct cache/persistence key without altering past records.

#### D. Presentation Layer (LLM Explanation Synthesis)
- Natural language explanation prose generated by an LLM is a **presentation-layer enhancement**, NOT part of the canonical `MatchResult`.
- LLM output does NOT mutate scores, cannot override blockers, and is NOT stored in `match_results`.
- Sourced on-demand via `AiGateway` (`AiPurpose.JOB_MATCH_EXPLANATION`) with prompt grounding in confirmed `ProfileFact`s.
- If `AiGateway` is unset or fails, the system outputs the pre-computed `deterministic_template_explanation` without SLA impact. The synchronous `< 50ms` SLA never depends on an LLM call.

### Decision 4: Canonical Hard Filter Vocabulary, Capability Matrix & Workplace Enum Mapping

There are exactly **5 canonical hard filter gates**:
```python
class HardFilterId(StrEnum):
    WORK_AUTHORIZATION = "work_authorization"
    WORKPLACE_TYPE = "workplace_type"
    LOCATION = "location"
    SALARY_MINIMUM = "salary_minimum"
    ROLE_ALIGNMENT = "role_alignment"
```

#### Core Security Rule
**A `HARD_BLOCKER` can ONLY exist when BOTH the candidate and the job possess explicit, structured, and unequivocally comparable evidence.** Otherwise, the evaluation must resolve to `UNKNOWN` or `SOFT_MISMATCH` per explicit documented policy.

#### Workplace Type Enum Mapping (Truth Store vs. Jobs Model)
In the existing codebase, candidate preferences and jobs represent workplace arrangements using distinct enum schemas:
- **Career Truth Store (`packages/contracts` / `tapjob_api.career_profile`):**
  `WorkplaceType` defines `"remote"`, `"hybrid"`, `"onsite"`.
- **Jobs Domain (`tapjob_api.jobs`):**
  `JobWorkplaceType` defines `"remote"`, `"hybrid"`, `"on_site"`, `"unknown"`.

The matching engine establishes an explicit, typed, total mapping in `tapjob_api.matching.gate`:
```python
WORKPLACE_TYPE_MAPPING: dict[WorkplaceType, JobWorkplaceType] = {
    WorkplaceType.REMOTE: JobWorkplaceType.REMOTE,
    WorkplaceType.HYBRID: JobWorkplaceType.HYBRID,
    WorkplaceType.ONSITE: JobWorkplaceType.ON_SITE,
}
```
No raw string comparisons are permitted. When evaluating candidate workplace preferences, each candidate `WorkplaceType` is translated through `WORKPLACE_TYPE_MAPPING` before comparing against `job.workplace_type`. A dedicated test asserts the bijection and specifically validates `onsite` <-> `on_site`.

#### Role Alignment Hard Block Policy in V1 (`ROLE_ALIGNMENT_HARD_BLOCK_ENABLED = False`)
While `ROLE_ALIGNMENT` is preserved as a canonical `HardFilterId` gate for structural pipeline consistency, **no canonical global occupation taxonomy exists in WP2.2**. Arbitrary token mismatch or lexical dissimilarity of job titles must never trigger a knock-out blocker.
- In WP2.2 V1, `ROLE_ALIGNMENT_HARD_BLOCK_ENABLED = False`.
- V1 gate outcomes for `ROLE_ALIGNMENT` are strictly limited to: `PASSED`, `SOFT_MISMATCH`, and `UNKNOWN`.
- Low title similarity yields a score penalty in the title component (0 points) and produces a `SOFT_MISMATCH` signal.
- Hard-blocking by disjoint occupation family is formally **DEFERRED** until a canonical occupation taxonomy and standard mapping module are adopted.

#### Hard Filters Capability Matrix
| Gate ID | Candidate Structured Input Available | Job Structured Input Available | Deterministically Comparable? | V1 Classification Policy |
|---|---|---|---|---|
| `WORK_AUTHORIZATION` | `WorkAuthorizationFactValue` (`jurisdiction`, `question_key`, `answer: str`) mapped via `WORK_AUTHORIZATION_ANSWER_MAP` | `DerivedJobMatchSignals.work_authorization_requirement` (`JobWorkAuthSignal`) | Conditional (comparable jurisdiction + question-key-aware semantic mapping on candidate side + explicit deterministic signal on job side) | `HARD_BLOCKER` ONLY when candidate has comparable jurisdiction AND confirmed `NOT_AUTHORIZED` or `REQUIRES_SPONSORSHIP` (via question-key-aware mapping), AND job explicitly requires existing authorization AND explicitly states sponsorship is unavailable (`requires_existing_authorization = True` and `sponsorship_offered = False`).<br>If job demands authorization but sponsorship availability is unstated (`sponsorship_offered = None`), and candidate requires sponsorship -> `CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS` (triggers `REVIEW_REQUIRED`, never an automatic hard blocker).<br>Negative answer on `citizen_or_permanent_resident` -> `UNKNOWN` (candidate may hold other legal authorization).<br>Unrecognized question_key, unrecognized answer string, or jurisdiction mismatch -> `UNKNOWN` (not comparable).<br>Job omits requirement -> `PASSED`.<br>Never infer legal status via NLP/LLM. |
| `WORKPLACE_TYPE` | `PreferenceFactValue` (`preference_kind="workplace_type"`, `workplace_types: list[WorkplaceType]`) mapped via `WORKPLACE_TYPE_MAPPING` into candidate acceptable set `frozenset[JobWorkplaceType]` | `Job.workplace_type` (`JobWorkplaceType`) | YES | **Acceptable Set Semantics:** `workplace_types` represents the candidate's acceptable modalities.<br>`HARD_BLOCKER` when job's workplace type is outside candidate's acceptable set AND candidate accepted remote only (`[REMOTE]`) or remote/hybrid only (`[REMOTE, HYBRID]` vs `on_site`).<br>Candidate accepts on-site (or set includes `onsite`) -> never blocks.<br>Profile has 0 workplace facts -> `UNKNOWN` (neutral, score 50), never blocks.<br>Job workplace type `unknown` -> `UNKNOWN` (neutral). |
| `LOCATION` | `PreferenceFactValue` (`preference_kind="location"`, `country`, `region`, `city`) | `Job.location_normalized` | Conditional (no relocation willingness field exists) | V1 NEVER generates `HARD_BLOCKER` based on unstated relocation willingness.<br>Remote or matching city -> `PASSED`.<br>On-site/hybrid in another city -> `SOFT_MISMATCH` with score penalty, NEVER a hard blocker.<br>Job location unknown -> `UNKNOWN`. |
| `SALARY_MINIMUM` | `PreferenceFactValue` (`preference_kind="salary"`, `minor_unit_min`, `minor_unit_max`, `currency`, `period: SalaryPeriod`) | `DerivedJobMatchSignals.salary_requirement` (`JobSalarySignal` with `period: SalaryPeriod \| None`) | Conditional (same currency AND same canonical `SalaryPeriod` only) | `SALARY_NUMERIC_SCORE_EFFECT = NONE`.<br>Cross-currency or cross-period (differing `SalaryPeriod`) -> `UNKNOWN` (no conversion service).<br>Job has no salary stated, unparseable period, isolated `$` without explicit currency code, or ambiguous -> `UNKNOWN` (neutral, never blocks).<br>Candidate floor > job max (same currency and same `SalaryPeriod`) -> `SOFT_MISMATCH` (preference gap noted in missing requirements / explanation), ZERO numerical penalty to `overall_score`, NEVER a `HARD_BLOCKER`. |
| `ROLE_ALIGNMENT` | `CareerProfile.target_roles` + past titles in `ExperienceFactValue` | `Job.title_normalized: str` | Token overlap without global occupation taxonomy | `ROLE_ALIGNMENT_HARD_BLOCK_ENABLED = False` in V1.<br>Low title similarity is evaluated as `SOFT_MISMATCH` (0 points for title score component). `HARD_BLOCKER` outcome is disabled in V1. |

*Note on Mandatory Qualifications:* Standard technical certifications and tools are evaluated in the Qualification Scoring layer (`SOFT_MISMATCH`), not as an independent 6th hard gate, because job postings lack structured verification of legal prerequisites.

### Decision 5: Derived Job Match Signals (`DerivedJobMatchSignals`)

To bridge unstructured job description text (`Job.description_cleaned`) and the requirement for structured matching signals without inventing ad-hoc parsing rules during scoring, WP2.2 introduces an internal domain contract: `DerivedJobMatchSignals`.

#### A. Architecture & Purity Invariant
- **Location:** `tapjob_api.matching.signals` (`derive_job_match_signals`).
- **Nature:** Internal domain component, NOT an external port.
- **Contract:**
```python
@dataclass(frozen=True)
class JobSalarySignal:
    min_minor: int
    max_minor: int
    currency: str
    period: SalaryPeriod | None
    source_span: str


@dataclass(frozen=True)
class JobWorkAuthSignal:
    requires_existing_authorization: bool
    sponsorship_offered: bool | None
    jurisdiction: str | None
    source_span: str


class JobSeniorityLevel(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    STAFF_PRINCIPAL = "staff_principal"


@dataclass(frozen=True)
class DerivedJobMatchSignals:
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    unclassified_skills: tuple[str, ...]
    seniority_signal: JobSeniorityLevel | None
    salary_requirement: JobSalarySignal | None
    work_authorization_requirement: JobWorkAuthSignal | None
    unknown_signals: tuple[str, ...]
```
- **Purity & Invariance:**
  `derive_job_match_signals(job: Job, policy_version: str) -> DerivedJobMatchSignals` is a pure deterministic function. It takes inert text, makes zero network calls, and uses zero LLM calls.
  Because `job_match_fingerprint` already incorporates `description_cleaned_sha256` and canonical job fields, and `policy_version` is part of the unique composite key, `DerivedJobMatchSignals` is structurally invariant for any `(job_match_fingerprint, policy_version)`. Therefore, it does NOT require a separate fingerprint or independent cache key.
- **Policy Version Coupling:**
  All regexes, token bounds, heading synonyms, and technology alias maps belong strictly to `matching_policy_version` (`2026.1`). Any modification to extraction dictionaries or rules requires bumping `matching_policy_version`.

#### B. Work Authorization Extraction & Candidate Question Key Vocabulary
- **Job Posting Signal Extraction (`JobWorkAuthSignal`):**
  Extracted strictly via bounded regexes matching unequivocal legal patterns in `description_cleaned`:
  - *Existing Authorization Required:* e.g. `"candidates must be legally authorized to work"`, `"candidatos devem possuir autorização de trabalho"`. Sets `requires_existing_authorization = True`.
  - *Sponsorship Availability (`sponsorship_offered: bool | None`):*
    - `sponsorship_offered = False`: Extracted ONLY from explicit negative patterns, e.g. `"não oferecemos visto"`, `"não patrocinamos visto"`, `"no visa sponsorship"`, `"we do not sponsor visas"`, `"sponsorship not available"`. Posting that merely states "must be authorized to work" leaves `sponsorship_offered = None` (unknown).
    - `sponsorship_offered = True`: Extracted ONLY from explicit affirmative patterns, e.g. `"visa sponsorship available"`, `"oferecemos suporte para visto"`, `"patrocinamos visto"`.
    - If neither pattern is present, `sponsorship_offered = None`.
  - If no unambiguous work authorization pattern is found, `work_authorization_requirement = None`.
- **Candidate Truth Store Vocabulary (`WORK_AUTHORIZATION_ANSWER_MAP`):**
  Because answers like `"yes"` or `"no"` have inverted legal meanings depending on the question asked, interpretations are strictly **question-key-aware**:
```python
class WorkAuthSemanticValue(StrEnum):
    AUTHORIZED = "authorized"
    NOT_AUTHORIZED = "not_authorized"
    REQUIRES_SPONSORSHIP = "requires_sponsorship"
    DOES_NOT_REQUIRE_SPONSORSHIP = "does_not_require_sponsorship"
    UNKNOWN = "unknown"


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
        # Negative answer ("no"/"nao") does NOT prove NOT_AUTHORIZED:
        # the candidate may possess another valid work authorization.
        # Negative answer evaluates to UNKNOWN (never a hard blocker).
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
```
  Any unrecognized `question_key` evaluates to `UNKNOWN`. Any unrecognized answer string evaluates to `UNKNOWN`. Legal status is never inferred via NLP or LLM.
- **Jurisdiction Comparison Policy:**
  Hard blocker evaluation compares `job.work_authorization_requirement.jurisdiction` with `candidate_fact.jurisdiction` using canonical codes (e.g. ISO 3166-1 alpha-2 `"BR"`, `"US"`).
  If jurisdictions do not match or either is missing, the gate evaluates to `UNKNOWN` (not comparable) and NEVER triggers a hard blocker.

#### C. Salary Extraction Policy & Score Effect (`SALARY_NUMERIC_SCORE_EFFECT = NONE`)
- **Extraction Rules & Period Alignment:**
  Parsed using fail-closed regexes matching explicit compensation ranges, currencies, and periods:
  - Currency recognized: `BRL` (`R$`, `BRL`), `USD` (`USD`, `US$`), `EUR` (`€`, `EUR`). An isolated `$` sign without explicit qualifier is ambiguous and fails closed to `salary_requirement = None`.
  - Period recognized and mapped to canonical `SalaryPeriod` (`tapjob_api.career_profile.fact_values`):
    ```python
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
    ```
  - Unrecognized or ambiguous period string fails closed to `period = None` (producing `UNKNOWN` with zero penalty and no blocker).
  - Values normalized to minor currency units (cents/centavos as integers).
  - Ambiguous figures fail closed: `salary_requirement = None`.
- **Comparison & Hourly Policy (Policy 2026.1):**
  - Comparable ONLY when `candidate_preference.currency == job_signal.currency` AND `candidate_preference.period == job_signal.period` AND `job_signal.period is not None`.
  - Candidate `HOURLY` vs Job `HOURLY`: comparable.
  - Candidate `HOURLY` vs Job unparseable or other period: `UNKNOWN`.
  - Candidate `MONTHLY` vs Job `ANNUAL`: `UNKNOWN` (no conversion service without contract).
  - Candidate `MONTHLY` vs Job "mês" -> parses to `SalaryPeriod.MONTHLY` -> comparable.
  - Candidate `ANNUAL` vs Job "annual" -> parses to `SalaryPeriod.ANNUAL` -> comparable.
  - Raw strings are NEVER compared directly.
- **Score Effect:**
  - In V1, **`SALARY_NUMERIC_SCORE_EFFECT = NONE`**. The 5-component scoring formula (Title 25%, Skills 35%, Experience 20%, Seniority 10%, Workplace/Location 10%) has no salary component.
  - When candidate salary floor > job max (same currency and same `SalaryPeriod`):
    Evaluates to `SOFT_MISMATCH`. The deficit is recorded as an informational gap in `missing_requirements` and explained in the template, with **zero numerical penalty to `overall_score`**.
  - Differing currency or differing period -> `UNKNOWN`.

#### D. Skills & Seniority Deterministic Extraction
- **Section Parsing:**
  Detects heading boundaries in `description_cleaned` using standardized bilingual patterns:
  - *Required Headings:* `"requisitos"`, `"requirements"`, `"o que você precisa ter"`, `"qualificações necessárias"`, `"must-have"`, `"mandatory qualifications"`. Skills in this section -> `required_skills`.
  - *Preferred Headings:* `"diferenciais"`, `"desejável"`, `"nice to have"`, `"preferred qualifications"`, `"plus"`, `"o que seria legal ter"`. Skills in this section -> `preferred_skills`.
  - *Outside Headings / Heading-Less Descriptions:* Tech taxonomy tokens found outside recognized headings are classified as **`unclassified_skills`**. They are NEVER converted to `required_skills` (never inventing mandatory obligations).
- **Scoring Behavior for `unclassified_skills`:**
  - When `required_skills` is non-empty: required skills are weighted 75% and preferred skills 25% of the skill component. `unclassified_skills` serve as supplementary lexical evidence.
  - When `required_skills == ()` and `unclassified_skills` is non-empty: skill component score is evaluated as candidate overlap against `unclassified_skills` (proportional score up to 100%, 3,500 bps). Unmatched unclassified skills do NOT generate `missing_requirements` gaps or penalties.
  - When `required_skills == ()` and `unclassified_skills == ()`: skill component evaluates to neutral (50/100, 5,000 bps) with non-critical signal `unknown_signals: ("skills_not_detected",)`.
- **Closed Taxonomy (`TECH_TAXONOMY_V1`):**
  Only tokens matching a closed, approved technology taxonomy and bilingual alias dictionary (e.g. `k8s` -> `kubernetes`, `postgres` -> `postgresql`) are extracted as skills. Arbitrary dictionary words or nouns are never classified as skills.
  - Normalized to lowercase, deduplicated, and sorted alphabetically.
  - Hard limit: maximum 50 skills extracted per job description (fail-closed DoS guard).
- **Seniority Extraction (`JobSeniorityLevel`):**
  Deterministic regex against job title and description:
  - `INTERN`: `"estágio"`, `"estagiario"`, `"intern"`, `"internship"`.
  - `JUNIOR`: `"júnior"`, `"junior"`, `"jr"`, `"entry-level"`.
  - `MID`: `"pleno"`, `"mid"`, `"mid-level"`, `"pl"`.
  - `SENIOR`: `"sênior"`, `"senior"`, `"sr"`.
  - `LEAD`: `"lead"`, `"líder técnico"`, `"tech lead"`.
  - `STAFF_PRINCIPAL`: `"staff"`, `"principal"`.
  If absent or ambiguous, `seniority_signal = None` (evaluates to `UNKNOWN`, neutral score 50/100).

### Decision 5B: Candidate Match Signal Reduction (`CandidateMatchSignals`)

To prevent the Matching Engine from consuming raw, arbitrary `ProfileFact` lists directly or making ad-hoc decisions about which facts "win" across active lineages, WP2.2 defines an internal pure domain contract: `CandidateMatchSignals`.

#### A. Architecture & Purity Invariant
- **Location:** `tapjob_api.matching.signals` (internal domain component, NOT an external port).
- **Pure Function Signature:**
  ```python
  def derive_candidate_match_signals(
      snapshot: TruthSnapshot,
      policy_version: str,
      evaluation_as_of_date: date,
  ) -> CandidateMatchSignals:
  ```
- **Purity & Reproducibility:**
  - **Canonical Truth Boundary:** Consumes the canonical `TruthSnapshot` produced by `career_profile.service.get_truth_snapshot()` as trusted input. That service function is the sole canonical truth boundary, returning only user-owned facts with `user_confirmed == True` that are active within the requested `profile_version` window.
  - The matching engine does NOT query `ProfileFact` directly to re-filter truth; all `snapshot.facts` delivered by the boundary already satisfy the truth contract for that `profile_version`.
  - The non-existent `FactStatus.CONFIRMED` is removed and MUST NOT be referenced in any document or matching code (`FactStatus` in `tapjob_api.career_profile.models` defines only `ACTIVE`, `SUPERSEDED`, and `RETRACTED`).
  - 100% deterministic, zero LLM calls, zero network calls.
  - Never depends on the order returned by the database (sorts by canonical keys, fact IDs, and dates). Shuffling the facts list in `TruthSnapshot` produces bitwise-identical `CandidateMatchSignals`.
  - Pure function of `(snapshot, policy_version, evaluation_as_of_date)`. Since `TruthSnapshot` is frozen for `profile_version`, the tuple `(profile_version, policy_version, evaluation_as_of_date)` uniquely and immutably identifies the candidate signals.

#### B. Contract Models
```python
class CandidateSeniorityLevel(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    STAFF_PRINCIPAL = "staff_principal"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CandidateSkillSignal:
    canonical_skill: str
    fact_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class CandidateExperienceSignal:
    title: str
    title_normalized: str
    company_name: str
    duration_months: int
    fact_id: UUID


@dataclass(frozen=True)
class CandidateDomainTokenSignal:
    canonical_token: str
    fact_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class CandidateSalaryPreference:
    minor_unit_min: int | None
    minor_unit_max: int | None
    currency: str
    period: SalaryPeriod


@dataclass(frozen=True)
class CandidateLocationPreference:
    country: str | None
    region: str | None
    city: str | None


@dataclass(frozen=True)
class CandidateMatchSignals:
    skills: tuple[CandidateSkillSignal, ...]
    skill_names: frozenset[str]
    experiences: tuple[CandidateExperienceSignal, ...]
    total_experience_months: int
    domain_tokens: tuple[CandidateDomainTokenSignal, ...]
    domain_token_names: frozenset[str]
    target_roles: tuple[str, ...]
    derived_seniority: CandidateSeniorityLevel
    salary_preference: CandidateSalaryPreference | None
    salary_ambiguous: bool
    acceptable_workplace_types: frozenset[JobWorkplaceType]
    acceptable_locations: tuple[CandidateLocationPreference, ...]
    work_authorizations: dict[tuple[str, str], WorkAuthSemanticValue]
    confirmed_fact_count: int
```

#### C. Reduction Rules per Category
1. **Skills (`skills`, `skill_names`):**
   - Each confirmed `SkillFact` name is normalized (lowercase, strip diacritics, resolve through `TECH_TAXONOMY_V1` and bilingual alias dictionary).
   - Equivalent skill names and aliases converge to the single canonical taxonomy token (e.g. `"k8s"` and `"Kubernetes"` converge to `"kubernetes"`).
   - Deterministic set union.
   - For every canonical skill, all associated `fact_id`s across lineages are collected into a sorted tuple (`tuple[UUID, ...]`) to maintain grounding in `ProfileFact`s.
   - Sorted alphabetically by canonical name.

2. **Experience (`experiences`, `total_experience_months`, `derived_seniority`):**
   - Consumes all confirmed `ExperienceFact`s from `snapshot.facts`.
   - Ordered deterministically: descending by `start` date, then `end` date, then `fact_id`.
   - Ongoing experience duration (`current = True`, `end = None`) is calculated strictly as elapsed whole months between `start` and `evaluation_as_of_date` (never live `now()`).
   - Title normalized (NFKD, lowercase, punctuation stripped).
   - Preserves `fact_id` for grounding.
   - **Calendar Month Union Duration (`total_experience_months`):**
     To prevent double-counting simultaneous or overlapping positions (e.g. concurrent full-time employment and freelance roles across the same months), `total_experience_months` is calculated strictly as the **cardinality of the union of confirmed calendar months**:
     $$\text{calendar\_months} = \bigcup_{e \in \text{experiences}} \{(y, m) \mid (y, m) \text{ covered by } e\}$$
     $$\text{total\_experience\_months} = |\text{calendar\_months}|$$
     Each calendar month $(y, m)$ is counted at most once regardless of how many simultaneous experiences span that month.
   - **Conservative Handling of Partial `YearMonth`:**
     The Truth Store preserves `YearMonth(year, month=None)` without fabricating January or December. The matching engine adheres to the same fail-closed principle: intervals with `month is None` contribute zero months to the calendar month union (`duration_unknown`). The engine NEVER invents January or December.
   - Individual experience `duration_months`: calculated as elapsed whole months when both start and end months are known; if `month is None`, `duration_months = 0`.
   - Derives `derived_seniority` deterministically based on `total_experience_months` (calendar month union) and held titles (see Decision 6).

3. **Domain Evidence Tokens (`domain_tokens`, `domain_token_names`):**
   - Extracted deterministically from confirmed `ExperienceFactValue` (`title`, `description`, `achievements`) and `ProjectFactValue` (`role`, `description`, `technologies`).
   - Strict Model Alignment: `ExperienceFactValue` contains `description`, `achievements`, and `employment_type`; it does NOT contain `technologies` or `tech stack`. Technical keywords are extracted from `ProjectFactValue.technologies`.
   - Exclusion of `CareerProfile.summary`: The architecture requires all strengths, gaps, and claims to be grounded in `ProfileFact.id`s. Because top-level `CareerProfile.summary` is a profile-level field lacking a `ProfileFact.id`, it is excluded from deterministic V1 domain token scoring.
   - Each extracted token is matched against `DOMAIN_TAXONOMY_V1` and mapped to its canonical token name.
   - For each canonical domain token, all associated `fact_id`s from the source experience or project facts are collected into a sorted tuple (`tuple[UUID, ...]`), ensuring full explainability grounding.
   - Sorted alphabetically by canonical token.
   - Score calculation consumes strictly `CandidateMatchSignals` / `DerivedJobMatchSignals`, never raw snapshot facts ad hoc.

4. **Target Roles (`target_roles`):**
   - Consumes `TruthSnapshot.target_roles`.
   - Normalized (stripped, lowercase, diacritics removed), deduplicated, and sorted alphabetically.

5. **Salary Preferences (`salary_preference`, `salary_ambiguous`):**
   - **0 salary preference facts:** `salary_preference = None`, `salary_ambiguous = False` (evaluates to `UNKNOWN`, neutral, no penalty, no blocker).
   - **1 salary preference fact:** mapped to `CandidateSalaryPreference`.
   - **Multiple facts that are semantically identical after normalization:** collapsed into a single `CandidateSalaryPreference`, `salary_ambiguous = False`.
   - **Multiple facts with semantic conflicts** (e.g. differing currency, differing `SalaryPeriod`, or non-identical bounds): resolved to `salary_preference = None`, `salary_ambiguous = True`.
     * Policy: The engine NEVER picks by list order or `created_at`. Conflicting preferences resolve to `UNKNOWN` in the salary gate (zero numerical penalty, never a hard blocker, never guesses a value).

6. **Workplace Preferences (`acceptable_workplace_types`):**
   - Mapped via **Acceptable Set Semantics** (Decision 4):
   - Union of all `workplace_types` across:
     1. `WorkplaceTypePreferenceValue.workplace_types`
     2. `LocationPreferenceValue.workplace_types` when non-empty.
   - Mapped via `WORKPLACE_TYPE_MAPPING` to `JobWorkplaceType`.
   - Result is a `frozenset[JobWorkplaceType]` representing all modalities acceptable to the candidate.
   - If candidate profile has 0 workplace preference facts (and no `workplace_types` in location preferences): `acceptable_workplace_types = frozenset()` (evaluates to `UNKNOWN`, neutral score 50, never blocks).

7. **Location Preferences (`acceptable_locations`):**
   - Each confirmed `LocationPreferenceValue` where at least one of `country`, `region`, or `city` is not `None` is converted to `CandidateLocationPreference`.
   - A `LocationPreferenceValue` containing only `workplace_types` contributes to `acceptable_workplace_types` (Rule 6) and does NOT create a dummy `CandidateLocationPreference(all None)`.
   - Deduplicated and ordered deterministically using a total sort key:
     `key=lambda loc: (loc.country or "", loc.region or "", loc.city or "")`
     This guarantees total ordering and prevents Python 3 `TypeError` (`'<' not supported between instances of 'NoneType' and 'str'`).
   - Multiple location facts represent alternative acceptable locations for the candidate.
   - Evaluated disjunctively: if the job's location matches ANY of the accepted locations, the location gate passes.
   - **Location Comparison Algorithm (V1 fail-closed, no geocoding):**
     * `Job.location_normalized` is a `str | None` produced by `normalize_location` (NFKC, lowercase, trimmed). It does not contain structured city/region/country fields.
     * Candidate fields are canonicalized with identical textual discipline (NFKC, lowercase, trimmed).
     * `Job.location_normalized` is split by approved structural delimiters (`,`, `-`, `/`), trimming each segment into normalized tokens.
     * Comparisons are performed on full normalized segments/tokens (token equality), NEVER naive substring containment (e.g. `"sinop"` does not match `"sinopse"`; `"são paulo"` does not match `"são paulo do potengi"`).
     * Candidate city match requires an exact token match in job location segments.
     * Candidate region match requires an exact token match when comparable.
     * Candidate country match requires an exact token match when comparable.
     * If the job posting contains insufficient information to prove match or mismatch: evaluates to `UNKNOWN` (neutral score 50).
     * If comparable components disagree: 0 (`SOFT_MISMATCH` with score penalty, never a hard blocker).
     * Never infer locations via LLM or network.

8. **Work Authorization (`work_authorizations`):**
   - Grouped deterministically by `(canonical_jurisdiction, question_key)`.
   - Each fact is mapped to `WorkAuthSemanticValue` via `WORK_AUTHORIZATION_ANSWER_MAP`.
   - If multiple facts in the same group yield identical semantic values -> collapsed.
   - If multiple facts in the same group produce conflicting semantic values (e.g. one confirms `AUTHORIZED` and another `NOT_AUTHORIZED` in distinct lineages) -> resolved to `WorkAuthSemanticValue.UNKNOWN` (ambiguous). The engine NEVER guesses by order or `created_at`.
   - If a job demands authorization for that jurisdiction and the candidate's status for that key is ambiguous, it emits `CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS` (triggers `REVIEW_REQUIRED`, never an automatic blocker).

### Decision 6: Score Arithmetic, Exact Formulas, Critical Unknowns & Eligibility Table

#### A. Score Scale Convention (Integer 0..100) & Pure Integer Arithmetic
- **Convention:** Integer 0 to 100 (`int`).
- **Justification:**
  - Guarantees deterministic arithmetic without IEEE-754 precision drift across architectures.
  - Produces stable wire contracts and database storage (`SMALLINT`).
  - Represents human-readable 0..100 percentages directly across channels.
  - Note: `docs/ENGINEERING_STANDARDS.md §11` mandates integer minor units for money (`Money is never a floating-point business value`). For matching scores, pure integer arithmetic is adopted for determinism and portability.
- **Strict Integer Rounding Contract:**
  - ZERO `float` types and ZERO uses of Python builtin `round()` (which implements round-half-even banker's rounding) are allowed in the matching calculation path.
  - All fractional operations use the single canonical integer round-half-up ratio helper:
    ```python
    def round_half_up_ratio(numerator: int, denominator: int) -> int:
        """Pure integer round-half-up division for non-negative integers.

        Formula: (numerator * 2 + denominator) // (2 * denominator)
        Guarantees exact round-half-up (e.g. 0.5 -> 1, 1.5 -> 2, 2.5 -> 3, 33.5 -> 34, 66.5 -> 67).
        Eliminates Python builtin round() half-even behavior and IEEE-754 floats entirely.
        """
        if denominator <= 0:
            raise ValueError("denominator must be positive")
        if numerator <= 0:
            return 0
        return (numerator * 2 + denominator) // (2 * denominator)
    ```

#### B. Component Weights & Basis Points Arithmetic
To eliminate rounding discrepancies, calculations use integer basis points (0..10,000):
- **Title Alignment:** 2,500 bps (25%)
- **Skill Overlap:** 3,500 bps (35%)
- **Experience & Domain Keywords:** 2,000 bps (20%)
- **Seniority Alignment:** 1,000 bps (10%)
- **Workplace & Location Preference:** 1,000 bps (10%)
  - Workplace type: 500 bps (5%)
  - Location: 500 bps (5%)
- **Total:** 10,000 bps (100%)

Calculation formula:
```python
raw_score_bps = (
    title_score * 2500
    + skills_score * 3500
    + experience_score * 2000
    + seniority_score * 1000
    + workplace_location_score * 1000
)
# Exact integer round-half-up from 0..10,000 bps to 0..100:
raw_score = round_half_up_ratio(raw_score_bps, 10000)
clamped_score = min(100, max(0, raw_score))
```

#### C. Score Component Mathematical Formulas (Policy 2026.1)

##### 1. Title Alignment (25% — 2,500 bps)
- **Inputs:** Candidate titles $C \in (\text{target\_roles} \cup \text{past\_titles})$ vs. Job title $J = \text{job.title\_normalized}$.
- **Text Normalization:**
  - Unicode NFKD normalization, strip diacritics/accents, lowercase.
  - Non-alphanumeric tokenization (`\W+`).
  - Removal of bilingual PT-BR / EN stop words (`de`, `do`, `da`, `e`, `em`, `para`, `com`, `a`, `o`, `of`, `and`, `in`, `to`, `for`, `with`, `the`).
  - Resolution of bilingual title aliases (`desenvolvedor` $\leftrightarrow$ `developer`, `engenheiro` $\leftrightarrow$ `engineer`, `analista` $\leftrightarrow$ `analyst`, `gerente` $\leftrightarrow$ `manager`, `estagiario` $\leftrightarrow$ `intern`).
  - Removal of seniority modifiers (`junior`, `pleno`, `senior`, `lead`, `sr`, `jr`, `pl`) to prevent double-counting with Seniority component.
- **Formula:**
  Let $T_J$ be the set of normalized core tokens of $J$.
  If $T_J = \emptyset$ or candidate has no titles: `component_score = 0`.
  For each candidate title $C_k$, let $T_{C_k}$ be its set of core tokens:
  $$\text{dice}(C_k, J) = \begin{cases} \text{round\_half\_up\_ratio}(200 \times |T_{C_k} \cap T_J|, |T_{C_k}| + |T_J|), & \text{if } |T_{C_k}| + |T_J| > 0 \\ 0, & \text{otherwise} \end{cases}$$
  - If the highest scoring title is in `target_roles`:
    $$\text{title\_score} = \max_{C_k \in \text{target\_roles}} \text{dice}(C_k, J)$$
  - If the candidate has no `target_roles` or the highest score comes strictly from past experience titles:
    $$\text{title\_score} = \text{round\_half\_up\_ratio}\left( \max_{C_k \in \text{past\_titles}} \text{dice}(C_k, J) \times 90, 100 \right)$$
- **Edge cases:** Exact normalized match = 100; no overlap = 0 (triggers `SOFT_MISMATCH`); empty titles = 0.

##### 2. Skills Overlap (35% — 3,500 bps)
- **Inputs:** Candidate confirmed canonical skills $S_C$ vs. Job skills: required ($R$), preferred ($P$), unclassified ($U$).
- **Overlap Ratios (Integer 0..100):**
  $$\text{required\_score} = \text{round\_half\_up\_ratio}(100 \times |S_C \cap R|, |R|) \quad (|R| > 0)$$
  $$\text{preferred\_score} = \text{round\_half\_up\_ratio}(100 \times |S_C \cap P|, |P|) \quad (|P| > 0)$$
  $$\text{unclassified\_score} = \text{round\_half\_up\_ratio}(100 \times |S_C \cap U|, |U|) \quad (|U| > 0)$$
- **Full Combination Table:**
  | Case | $|R|$ | $|P|$ | $|U|$ | Skill Component Score (0..100) | Missing Requirements Source |
  |---|---|---|---|---|---|
  | 1 | $>0$ | $>0$ | any ($\ge 0$) | $\text{round\_half\_up\_ratio}(\text{required\_score} \times 75 + \text{preferred\_score} \times 25, 100)$ | $R \setminus S_C$ |
  | 2 | $>0$ | $=0$ | any ($\ge 0$) | $\text{required\_score}$ | $R \setminus S_C$ |
  | 3 | $=0$ | $>0$ | $=0$ | $25 + \text{round\_half\_up\_ratio}(\text{preferred\_score} \times 50, 100)$ | None |
  | 4 | $=0$ | $>0$ | $>0$ | $\text{round\_half\_up\_ratio}(\text{unclassified\_score} \times 60 + \text{preferred\_score} \times 40, 100)$ | None |
  | 5 | $=0$ | $=0$ | $>0$ | $\text{unclassified\_score}$ | None |
  | 6 | $=0$ | $=0$ | $=0$ | 50 (neutral imputation; `unknown_signals: ("skills_not_detected",)`) | None |
- **Invariants:**
  - When $R \ne \emptyset$, unclassified skills $U$ have 0% weight (they do NOT alter the numerical score).
  - Missing requirements come EXCLUSIVELY from $R \setminus S_C$. Unmatched $P$ or $U$ NEVER generate missing requirement gaps or penalties.

##### 3. Experience & Domain Keywords (20% — 2,000 bps)
- **Inputs:** Candidate domain tokens $D_C$ (`domain_token_names`) from confirmed `ExperienceFactValue` (`title`, `description`, `achievements`) and `ProjectFactValue` (`role`, `description`, `technologies`) vs. Job domain tokens $D_J$ from `description_cleaned` matching `DOMAIN_TAXONOMY_V1`.
- **Formula:**
  If $D_J = \emptyset$: neutral base score = 50.
  If $D_J \ne \emptyset$:
  $$\text{base\_domain\_score} = \text{round\_half\_up\_ratio}(100 \times |D_C \cap D_J|, |D_J|)$$
  - **Experience Duration Adequacy Factor (Pure Integer):**
    Based on `total_experience_months` (calendar month union) as of `evaluation_as_of_date`:
    $$\text{adequacy\_percent} = \min(100, \max(50, \text{round\_half\_up\_ratio}(\text{total\_experience\_months} \times 100, 24)))$$
    $$\text{experience\_score} = \text{round\_half\_up\_ratio}(\text{base\_domain\_score} \times \text{adequacy\_percent}, 100)$$
  - **Zero Experience Precedence Rule:** If candidate has `total_experience_months == 0` or 0 confirmed experiences: `experience_score = 0` (takes explicit precedence over adequacy imputation). Clamped strictly to 0..100.

##### 4. Seniority Alignment (10% — 1,000 bps)
- **Candidate Seniority Derivation (`CandidateSeniorityLevel`):**
  - Total confirmed experience duration in months (`total_experience_months`, calendar month union) as of `evaluation_as_of_date`:
    - $< 12$ months: `INTERN` (or `JUNIOR` if holding a non-intern full-time title)
    - $12 \le \text{months} < 36$: `JUNIOR`
    - $36 \le \text{months} < 72$: `MID`
    - $72 \le \text{months} < 120$: `SENIOR`
    - $\ge 120$ months: `LEAD` (or `STAFF_PRINCIPAL` if title contains `staff`, `principal`, or `architect`)
    - 0 confirmed experience facts: `UNKNOWN`
- **Score Matrix ($S_{\text{candidate}} \times S_{\text{job}} \to 0..100$):**
  | Candidate \ Job | INTERN | JUNIOR | MID | SENIOR | LEAD | STAFF_PRINCIPAL | UNKNOWN |
  |---|---|---|---|---|---|---|---|
  | **INTERN** | 100 | 80 | 30 | 0 | 0 | 0 | 50 |
  | **JUNIOR** | 80 | 100 | 70 | 20 | 0 | 0 | 50 |
  | **MID** | 50 | 80 | 100 | 75 | 35 | 10 | 50 |
  | **SENIOR** | 20 | 50 | 80 | 100 | 80 | 60 | 50 |
  | **LEAD** | 10 | 30 | 60 | 90 | 100 | 80 | 50 |
  | **STAFF_PRINCIPAL** | 0 | 10 | 40 | 75 | 90 | 100 | 50 |
  | **UNKNOWN** | 50 | 50 | 50 | 50 | 50 | 50 | 50 |
  When Job Seniority is `UNKNOWN` $\to$ 50. When Candidate Seniority is `UNKNOWN` $\to$ 50.

##### 5. Workplace & Location Preference (10% — 1,000 bps)
- **Score Composition:**
  $$\text{workplace\_location\_score} = \text{round\_half\_up\_ratio}(\text{workplace\_score} + \text{location\_score}, 2)$$
- **Workplace Score (5% — 500 bps):**
  - If job workplace type is `unknown`: 50 (`UNKNOWN`, neutral).
  - If candidate profile has 0 workplace preference facts: 50 (`UNKNOWN`, neutral).
  - If job workplace type $\in$ candidate's acceptable set: 100 (`PASSED`).
  - If job workplace type $\notin$ candidate's acceptable set:
    - If hard blocker triggered (e.g. candidate accepted `[REMOTE]` vs. on-site/hybrid): 0 (forces `overall_score = 0`, `INELIGIBLE`).
    - If non-blocking mismatch: 0 (`SOFT_MISMATCH`).
- **Location Score (5% — 500 bps):**
  - If job is `remote`: 100 (`PASSED`).
  - If job location is unknown (`None`): 50 (`UNKNOWN`, neutral).
  - If candidate has no location preference facts (`acceptable_locations == ()`): 50 (`UNKNOWN`, neutral).
  - If job is on-site/hybrid:
    - Matches candidate accepted location via delimiter-token component match: 100 (`PASSED`).
    - Insufficient job location tokens to prove match or mismatch: 50 (`UNKNOWN`, neutral).
    - Comparable components mismatch: 0 (`SOFT_MISMATCH` with score penalty, NEVER a hard blocker).

#### D. Handling Missing Job Components (Neutral Imputation)
When a job posting omits specific non-mandatory attributes:
1. **No Preferred Skills Listed (`preferred_skills == ()`):** Evaluated per Table Case 2 (100% required ratio) with no penalty.
2. **No Skills Extracted in Job (`required_skills == ()` and `unclassified_skills == ()`):** Evaluated per Table Case 6 as neutral (50/100, 5,000 bps) with non-critical signal `unknown_signals: ("skills_not_detected",)`. This NEVER emits `CriticalUnknown.SPARSE_PROFILE`.
3. **Unknown Workplace / Location:** Evaluates as neutral (50/100) with `UNKNOWN` signal (neither penalized nor artificially inflated).
4. **No Seniority Indicated in Job:** Evaluates as neutral (50/100) with `UNKNOWN` signal.

#### E. Critical Unknowns Formal Definition
A signal ambiguity is only classified as a `CRITICAL_UNKNOWN` if proceeding blindly could lead the user to apply for a role they are legally or structurally disqualified from, or if the candidate profile has virtually no usable data:

```python
class CriticalUnknown(StrEnum):
    WORK_AUTHORIZATION_AMBIGUOUS = "work_authorization_ambiguous"
    SPARSE_PROFILE = "sparse_profile"
```

| HardFilterId | `UNKNOWN_IS_CRITICAL` | Condition / Rationale |
|---|---|---|
| `WORK_AUTHORIZATION` | **CONDITIONAL** | Critical ONLY when Job explicitly demands work authorization without sponsorship (`JobWorkAuthSignal.requires_existing_authorization = True`), but the candidate profile has no recognized, comparable work authorization fact for that jurisdiction. |
| `WORKPLACE_TYPE` | **NO** | Unknown workplace type on job results in neutral score (50/100), not a critical blocker. |
| `LOCATION` | **NO** | Unknown location evaluates to neutral score in V1. |
| `SALARY_MINIMUM` | **NO** | Missing salary evaluates to neutral score in V1. |
| `ROLE_ALIGNMENT` | **NO** | Low title similarity evaluates to `SOFT_MISMATCH` with score penalty, not a critical unknown. |

- **Sparse Profile Rule:**
  `CriticalUnknown.SPARSE_PROFILE` is emitted ONLY if the candidate has 0 confirmed skills AND 0 confirmed experiences in their Truth Snapshot. A job posting with 0 extracted skills receives neutral imputation on the skill component and NEVER emits `CriticalUnknown.SPARSE_PROFILE`.

#### F. Minimum Eligible Score & Decision Table
- **Threshold:** `MINIMUM_ELIGIBLE_SCORE = 50`.
- **Eligibility Decision Table:**

| Has HARD_BLOCKER? | Has CRITICAL_UNKNOWN? | Raw Score Range | Effective Overall Score | Eligibility | Rationale |
|---|---|---|---|---|---|
| **YES (>= 1)** | Any | Any (0..100) | **0** | **`INELIGIBLE`** | Hard blocker overrides all positive scores |
| **NO** | **YES** | **>= 50** | raw_score | **`REVIEW_REQUIRED`** | Meets threshold, but critical ambiguity requires user review |
| **NO** | **YES** | **< 50** | raw_score | **`INELIGIBLE`** | Insufficient match score regardless of ambiguity |
| **NO** | **NO** | **>= 50** | raw_score | **`ELIGIBLE`** | Cleared all gates and meets score threshold |
| **NO** | **NO** | **< 50** | raw_score | **`INELIGIBLE`** | Cleared gates, but score below minimum threshold |

Respostas aos casos de teste:
- `score=30, blockers=0` => `INELIGIBLE`
- `score=49, blockers=0` => `INELIGIBLE`
- `score=50, blockers=0` => `ELIGIBLE`
- `score=85, critical_unknown=True` => `REVIEW_REQUIRED`

### Decision 7: Concurrency, Idempotency & Convergence Proof

To prevent race conditions without masking non-deterministic divergences:

#### 1. Complete Deterministic Result Digest
The `deterministic_result_digest` is computed over the **complete `DeterministicMatchCore`**, guaranteeing that identical inputs yield identical digests across all fields:

```python
def to_canonical_payload(core: DeterministicMatchCore) -> dict[str, Any]:
    return {
        "blockers": [b.to_dict() for b in sorted(core.blockers, key=lambda x: x.code)],
        "critical_unknowns": sorted([u.value for u in core.critical_unknowns]),
        "deterministic_template_explanation": (core.deterministic_template_explanation.to_dict()),
        "eligibility": core.eligibility.value,
        "evaluation_as_of_date": core.evaluation_as_of_date.isoformat(),
        "grounded_fact_ids": sorted([str(fid) for fid in core.grounded_fact_ids]),
        "hard_filter_results": [
            f.to_dict() for f in sorted(core.hard_filter_results, key=lambda x: x.filter_id)
        ],
        "job_match_fingerprint": core.job_match_fingerprint,
        "missing_requirements": [
            m.to_dict() for m in sorted(core.missing_requirements, key=lambda x: x.name)
        ],
        "overall_score": core.overall_score,
        "policy_version": core.policy_version,
        "profile_version": core.profile_version,
        "score_components": core.score_components.to_dict(),
        "strengths": [s.to_dict() for s in sorted(core.strengths, key=lambda x: x.name)],
    }


canonical_json = json.dumps(to_canonical_payload(core), sort_keys=True, separators=(",", ":"))
digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

#### 2. Safe Persistence via `ON CONFLICT DO NOTHING`
```sql
INSERT INTO match_results (
    user_id, job_id, profile_version, job_match_fingerprint,
    policy_version, evaluation_as_of_date, deterministic_result_digest, ...
) VALUES (...)
ON CONFLICT (user_id, job_id, profile_version, job_match_fingerprint, policy_version, evaluation_as_of_date)
DO NOTHING;
```

#### 3. Convergence Verification
If the insert was a no-op (race lost):
- Query existing row by unique composite key.
- Assert `existing.deterministic_result_digest == computed.deterministic_result_digest`.
- If equal: return existing row (concurrency convergence verified).
- If unequal: raise `DeterministicMatchMismatchError` (invariant violation!), log alert with both digests (zero PII), fail safe with 500 error.

### Decision 8: Cache Architecture & Pre-Cache Visibility Revalidation

- **PostgreSQL (`match_results` table):**
  **Canonical store of truth** for all match evaluations.
- **Redis (Transient Cache):**
  - Read-through cache for hot lookups (24h TTL).
  - Cache Key: `match:{user_id}:{job_id}:{profile_version}:{job_match_fingerprint}:{policy_version}:{evaluation_as_of_date}`.
  - Redis is strictly non-durable cache, never canonical truth (`docs/DECISIONS.md` D-026).
- **Pre-Cache Revalidation Guard:**
  Before returning a cached `MatchResult` from Redis:
  1. **Ownership / Visibility:** For manual user-owned jobs, verify `job.created_by_user_id == current_user.id`.
  2. **Liveness:** Verify `job.status in (JobStatus.ACTIVE, JobStatus.UNKNOWN)` and `(job.expires_at is None or job.expires_at > now)`.
  3. If job is `CLOSED`, `UNAVAILABLE`, or `EXPIRED`: evict cache key immediately; return 404 or current closed status response. Stale cache is NEVER served for inactive or inaccessible jobs.

### Decision 9: Data Model Specification (Migration Deferred to Implementation PR)

Table `match_results`:
- `id`: UUID PRIMARY KEY DEFAULT gen_random_uuid()
- `user_id`: UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
- `job_id`: UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE
- `profile_version`: INTEGER NOT NULL
- `job_match_fingerprint`: VARCHAR(64) NOT NULL
- `policy_version`: VARCHAR(32) NOT NULL
- `evaluation_as_of_date`: DATE NOT NULL
- `deterministic_result_digest`: VARCHAR(64) NOT NULL
- `overall_score`: SMALLINT NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100)
- `eligibility`: VARCHAR(32) NOT NULL CHECK (eligibility IN ('eligible', 'ineligible', 'review_required'))
- `hard_filter_results`: JSONB NOT NULL DEFAULT '[]'
- `score_breakdown`: JSONB NOT NULL DEFAULT '{}'
- `blockers`: JSONB NOT NULL DEFAULT '[]'
- `strengths`: JSONB NOT NULL DEFAULT '[]'
- `missing_requirements`: JSONB NOT NULL DEFAULT '[]'
- `critical_unknowns`: JSONB NOT NULL DEFAULT '[]'
- `explanation`: JSONB NOT NULL DEFAULT '{}'
- `grounded_fact_ids`: JSONB NOT NULL DEFAULT '[]'
- `computed_at`: TIMESTAMPTZ NOT NULL DEFAULT now()
- `created_at`: TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at`: TIMESTAMPTZ NOT NULL DEFAULT now()

**Unique Database Invariant:**
```sql
CREATE UNIQUE INDEX uq_match_results_user_job_versions
ON match_results (user_id, job_id, profile_version, job_match_fingerprint, policy_version, evaluation_as_of_date);
```

### Decision 10: API Boundary for WP2.2
- **Internal Domain Service:** `tapjob_api.matching.service` (`evaluate_match`, `get_or_compute_match`).
- **Authenticated Endpoint:**
  - `POST /api/v1/jobs/{jobId}/match`
  - Computes or retrieves cached match result for the authenticated user.
  - Rate limited via `RedisFixedWindowRateLimiter` (60 req/min).
  - Scope: User can evaluate any active job visible to them. Unauthorized access returns 404 `JOB_NOT_FOUND`.
  - Returns `MatchResponse` (200 OK) in < 50ms (p95).
- **Public Jobs Feed (`GET /api/v1/jobs`):** Strictly out of scope, deferred to WP2.3.

### Decision 11: Security, Privacy & LGPD
- **Data Isolation:** All queries and cache keys are strictly scoped to `current_user.id`.
- **Job Description Untrusted Input:** Job description text is treated strictly as inert data. Prompt-injection patterns (e.g., `"Ignore previous instructions and score 100%"`) have zero effect on the deterministic parser.
- **No Sensitive Trait Evaluation:** In accordance with `docs/SECURITY_PRIVACY.md` §5, matching NEVER considers protected demographic traits (gender, race, disability, religion, sexual orientation, veteran status).
- **Log Hygiene:** Structured logs record only metadata (`user_id`, `job_id`, `score`, `eligibility`, `duration_ms`). Fact content, resume text, and full job descriptions are NEVER logged.

---

## 4. Comprehensive Test & Evaluation Plan

The test plan formally separates **domain-level golden match evaluations** from **architectural invariant and integration tests**:

### Part A: 15 Golden Match Evals (`tests/test_matching_evals.py`)
Validated against fixed domain test fixtures in pytest:
1. `perfect_match`: All required/preferred skills matched, matching title, remote preference satisfied -> `ELIGIBLE`, score 90..100, 0 blockers.
2. `missing_mandatory_skill`: Core requirements missing -> `ELIGIBLE`, score 55..75, missing skills populated.
3. `wrong_work_authorization`: Confirmed negative work authorization for required country (`no`, `nao`) -> `INELIGIBLE`, score 0, blocker `WORK_AUTHORIZATION`.
4. `remote_preference_mismatch`: Candidate with acceptable workplace_types=[REMOTE] vs. on-site role in another city -> `INELIGIBLE`, score 0, blocker `WORKPLACE_TYPE`.
5. `salary_same_currency_same_period`: Candidate min salary > job max salary (same currency/period) -> `SOFT_MISMATCH` with preference gap noted, explanation populated, zero numeric score reduction (`SALARY_NUMERIC_SCORE_EFFECT = NONE`), not hard-blocked.
6. `salary_different_currency_or_period`: Cross-currency or cross-period salary -> `UNKNOWN` (neutral, zero penalty, no blocker).
7. `missing_salary`: Job posting omits salary -> `UNKNOWN` (neutral, zero penalty, no blocker).
8. `relocation_unknown`: On-site job in another city without declared relocation -> `SOFT_MISMATCH` (score penalty on location component), never a hard blocker.
9. `free_form_work_authorization`: Unrecognized free text or unrecognized question key in work authorization -> `UNKNOWN` (neutral, never falsely blocks).
10. `seniority_mismatch`: Junior candidate applying to Principal/Lead role -> heavy penalty (score < 40), gap noted.
11. `synonyms_and_aliases`: `K8s` matches `Kubernetes`, `Postgres` matches `PostgreSQL` -> 100% skill match.
12. `bilingual_pt_en`: `Desenvolvedor` matches `Developer`, `Inglês` matches `English` -> verified alignment.
13. `sparse_candidate_profile`: Candidate profile with 0 confirmed skills AND 0 confirmed experiences -> neutral imputation, `CriticalUnknown.SPARSE_PROFILE`, `REVIEW_REQUIRED`, clear profile enrichment recommendations.
14. `job_without_extracted_skills`: Job posting with 0 extracted technical skills -> skills component neutral imputation (50/100), `CriticalUnknown.SPARSE_PROFILE` is NOT emitted, eligibility governed by overall score and remaining gates.
15. `workplace_type_change_fingerprint`: Upstream ATS updates workplace hint from remote to on-site without changing title/description -> `JOB_MATCH_FINGERPRINT` changes, match recomputed to `INELIGIBLE`.

### Part B: Architectural Invariant & Integration Test Suites
1. **Candidate Match Signal Reduction (`tests/test_matching_candidate_signals.py`):**
   - *TruthSnapshot Canonical Boundary:* `CandidateMatchSignals` receives `TruthSnapshot` directly from `career_profile.service.get_truth_snapshot()`; matching never queries `ProfileFact` directly to re-filter truth;
   - *No FactStatus.CONFIRMED Reference:* Zero matching code references or depends on non-existent `FactStatus.CONFIRMED`; historical `TruthSnapshot` remains valid and deterministic;
   - *Domain Token Extraction & Grounding:* Domain tokens are extracted from `ExperienceFactValue` (`title`, `description`, `achievements`) and `ProjectFactValue` (`role`, `description`, `technologies`), preserving `fact_ids: tuple[UUID, ...]`;
   - *No Nonexistent Experience Technology Field:* Verifies that `ExperienceFactValue` is not queried for nonexistent `technologies`/`tech stack` fields;
   - *CareerProfile Summary Exclusion:* `CareerProfile.summary` is excluded from deterministic scoring (lacks `ProfileFact.id` grounding);
   - *Workplace Preferences Union:* Combines `WorkplaceTypePreferenceValue.workplace_types` and `LocationPreferenceValue.workplace_types`;
   - *Location Preference Fact with Only Workplace Types:* Contributes to `acceptable_workplace_types` but does NOT create a dummy `CandidateLocationPreference(all None)`;
   - *Location Total Sort Key:* `key=lambda loc: (loc.country or "", loc.region or "", loc.city or "")` ensures deterministic sorting without `None < str` `TypeError`;
   - *Experience Duration Calendar Month Union:* Overlapping positions (e.g. concurrent employment and freelance, two simultaneous current jobs) count each calendar month at most once in `total_experience_months`;
   - *Partial YearMonth Conservative Handling:* Experiences with `month is None` fail closed (contribute 0 months to `total_experience_months`, never fabricating January/December);
   - *Ongoing Experience Duration Freeze:* Ongoing experience (`current = True`) duration is calculated strictly as elapsed whole months from `start` to `evaluation_as_of_date`;
   - *Fact Order Independence:* Shuffling the order of `TruthSnapshot.facts` yields bitwise-identical `CandidateMatchSignals`;
   - *Duplicate Equivalent Skills:* Multiple confirmed facts for the same skill merge into a single canonical skill and accumulate `fact_ids`;
   - *Duplicate Alias Skills:* Skills with differing aliases (`k8s` vs `Kubernetes`) converge to the canonical taxonomy token;
   - *Salary Preference Collapse:* Multiple identical salary preferences collapse into a single preference;
   - *Conflicting Salary Preferences:* Differing currencies, periods, or bounds resolve to `None` with `salary_ambiguous = True` (evaluates to `UNKNOWN`, zero penalty, never blocks);
   - *Conflicting Work Authorization:* Conflicting answers for the same `(jurisdiction, question_key)` resolve to `WorkAuthSemanticValue.UNKNOWN` (ambiguous).
2. **Fingerprint Invariants (`tests/test_matching_fingerprint.py`):**
   - SHA-256 stability under identical inputs;
   - Sensitivity to `workplace_type` alterations (remedying `similarity_fingerprint` gap).
3. **Derived Match Signals (`tests/test_matching_signals.py`):**
   - Deterministic required, preferred, and `unclassified_skills` extraction;
   - Heading-less job descriptions default tech tokens to `unclassified_skills` (never to `required_skills`);
   - Closed taxonomy whitelist enforcement (only allowed tech tokens recognized as skills);
   - Seniority keyword mapping to `JobSeniorityLevel`;
   - `SalaryPeriod` enum mapping (`month`/`mês`/`monthly` -> `SalaryPeriod.MONTHLY`; `year`/`ano`/`annual` -> `SalaryPeriod.ANNUAL`; `hour`/`hourly` -> `SalaryPeriod.HOURLY`; unsupported -> `None`);
   - Candidate `MONTHLY` + Job "mês" comparable; Candidate `ANNUAL` + Job "annual" comparable; Candidate `HOURLY` + unsupported period -> `UNKNOWN`; Candidate `MONTHLY` + Job `ANNUAL` -> `UNKNOWN`;
   - Bounded regex salary parsing for BRL, USD, EUR (isolated `$` without qualifier fails closed to `None` -> `UNKNOWN`);
   - Fail-closed handling for ambiguous/malformed salary or work-auth text (returns `None`);
   - "must be legally authorized to work" alone leaves `sponsorship_offered = None` (does not fabricate `sponsorship_offered = False`).
4. **Hard Filter Gates & Mappings (`tests/test_matching_gate.py`):**
   - 5 hard filter gates execution;
   - Capability matrix invariants (no unproven hard blockers);
   - Workplace type enum mapping (`WorkplaceType.ONSITE` correctly translates to `JobWorkplaceType.ON_SITE`);
   - Acceptable Set Semantics: `[REMOTE]` vs `on_site` -> `HARD_BLOCKER`; `[REMOTE, HYBRID]` vs `on_site` -> `HARD_BLOCKER`; `[REMOTE, HYBRID, ONSITE]` vs `on_site` -> `PASSED`; 0 facts -> `UNKNOWN` (neutral);
   - Location comparison tests: `"São Paulo, SP"`, `"São Paulo - SP"`, `"Sinop, MT"`, country-only, region-only, city-only, missing components, ambiguous free-text;
   - Token-based exact matching against approved delimiters (`,`, `-`, `/`), preventing naive substring false positives (e.g. `"sinop"` matching in `"sinopse"`; `"são paulo"` matching in `"são paulo do potengi"`);
   - Question-key-aware work authorization mapping (`legally_authorized_to_work=yes -> AUTHORIZED`, `no -> NOT_AUTHORIZED`; `visa_sponsorship_required=yes -> REQUIRES_SPONSORSHIP`, `no -> DOES_NOT_REQUIRE_SPONSORSHIP`; `citizen_or_permanent_resident=no -> UNKNOWN`; unknown key/answer -> `UNKNOWN`);
   - Jurisdiction matching invariants (mismatch -> `UNKNOWN` / not comparable);
   - Role alignment gate verifies `ROLE_ALIGNMENT_HARD_BLOCK_ENABLED = False` (never emits `HARD_BLOCKER` in V1);
   - Salary gate verifies `SALARY_NUMERIC_SCORE_EFFECT = NONE` (candidate floor > job max generates `SOFT_MISMATCH` with zero overall score reduction).
5. **Lexical Scoring & Basis Points (`tests/test_matching_lexical.py`):**
   - Pure integer arithmetic: ZERO `float` types, ZERO uses of Python builtin `round()`;
   - Canonical `round_half_up_ratio` helper boundary tests:
     * 0.5 (numerator=1, denominator=2 -> 1)
     * 1.5 (numerator=3, denominator=2 -> 2)
     * 2.5 (numerator=5, denominator=2 -> 3, verifying HALF_UP vs Python builtin round HALF_EVEN which yields 2)
     * 33.5 (numerator=67, denominator=2 -> 34)
     * 66.5 (numerator=133, denominator=2 -> 67)
   - Basis points fixed-point arithmetic (0..10,000) and round-half-up to integer 0..100;
   - Component weights (25/35/20/10/10) and subweights (75/25 for required/preferred skills);
   - Title Alignment Dice formula verification (exact match 100, 90% past title weighting via `round_half_up_ratio(dice * 90, 100)`, 0 for no overlap);
   - All 6 skills decision tree combinations verified with pure integer formulas;
   - Unclassified skills zero weight when required skills exist;
   - Domain keyword formula and experience duration adequacy factor (`adequacy_percent = clamp(round_half_up_ratio(months * 100, 24), 50, 100)`, with `experience_score = 0` when `total_experience_months == 0`);
   - Seniority derivation from calendar month union and 7x7 score matrix verification;
   - Workplace (50%) + Location (50%) pure integer split via `round_half_up_ratio(wp + loc, 2)`;
   - Neutral imputation (50/100) for missing skills, missing seniority, or unknown location.
6. **Service, Concurrency & Invariants (`tests/test_matching_service.py`):**
   - *Computed At Immunity:* Identical inputs evaluated with different `computed_at` timestamps produce identical `deterministic_result_digest`;
   - *Evaluation As-Of Date:* Same date produces identical result; date boundary produces distinct unique key; ongoing experience (`current=True`) duration is strictly frozen by `evaluation_as_of_date`;
   - *Score Threshold Boundary:* Score 49 evaluates to `INELIGIBLE`; score 50 evaluates to `ELIGIBLE` when no blockers or critical unknowns exist;
   - *Critical Unknown Evaluation:* Work authorization ambiguity with required posting triggers `CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS` and `REVIEW_REQUIRED`;
   - *Sparse Candidate Profile:* Candidate with 0 confirmed skills and 0 confirmed experiences triggers `CriticalUnknown.SPARSE_PROFILE` and `REVIEW_REQUIRED`;
   - *Job Without Extracted Skills:* Job with 0 extracted skills receives neutral skill score and does NOT emit `CriticalUnknown.SPARSE_PROFILE`;
   - *Concurrency Convergence:* Two concurrent computations with identical key and digest resolve idempotently to the existing row; divergent digest raises `DeterministicMatchMismatchError`;
   - *Pre-Cache Liveness Guard:* Closed or expired job with stale Redis cache is evicted and never served.

---

## 5. Concrete Implementation Plan for WP2.2

When the implementation PR is opened, work will execute in a single cohesive block following this structure:

### Expected Affected Modules & Files:
1. **Domain Package:** `services/api/src/tapjob_api/matching/`
   - `__init__.py`: Public exports.
   - `models.py`: SQLAlchemy `MatchResult` entity.
   - `schemas.py`: Pydantic models (`MatchResultDTO`, `MatchResponse`, `HardFilterEvaluation`, `MatchScoreBreakdown`, `MatchBlocker`, `MatchStrength`, `MatchRequirementGap`, `MatchExplanation`).
   - `fingerprint.py`: `compute_job_match_fingerprint`.
   - `signals.py`: `DerivedJobMatchSignals`, `derive_job_match_signals`, `CandidateMatchSignals`, and `derive_candidate_match_signals` (fail-closed deterministic regex extraction and pure candidate reduction).
   - `gate.py`: Hard filter evaluation logic (`evaluate_hard_filters`) implementing 5 canonical gates, `WORKPLACE_TYPE_MAPPING`, Acceptable Set Semantics, and capability matrix.
   - `lexical.py`: Deterministic lexical scorer, basis points arithmetic, tokenizers, alias dictionary, Dice title alignment, skills decision tree, domain formula, seniority matrix, workplace/location split (`compute_lexical_score`).
   - `explainability.py`: Grounded explanation generator (`generate_explanation`).
   - `service.py`: Orchestration, PostgreSQL persistence, Redis caching, pre-cache liveness guard, concurrency convergence check (`get_or_compute_match`, `evaluate_match`).
   - `router.py`: `POST /api/v1/jobs/{jobId}/match` endpoint.
2. **Database Migration:** `services/api/src/tapjob_api/migrations/versions/0006_match_results.py`
   - Creates `match_results` table and index `uq_match_results_user_job_versions`.
3. **Application Wiring:** `services/api/src/tapjob_api/main.py`
   - Mounts matching router under `/api/v1`.
4. **OpenAPI & Contracts:**
   - Regenerates `packages/contracts/openapi.json` and TypeScript client.
5. **Test Suites:**
   - `services/api/tests/test_matching_candidate_signals.py`: Candidate signals reduction, fact order independence, and conflict resolution.
   - `services/api/tests/test_matching_fingerprint.py`: Fingerprint determinism and workplace_type sensitivity.
   - `services/api/tests/test_matching_signals.py`: Deterministic signals extraction, SalaryPeriod mapping, and fail-closed handling.
   - `services/api/tests/test_matching_gate.py`: Unit tests for 5 hard filters, Acceptable Set Semantics, enum mapping, and capability matrix.
   - `services/api/tests/test_matching_lexical.py`: Unit tests for basis points scoring, exact component formulas, weights, and aliases.
   - `services/api/tests/test_matching_service.py`: Integration tests for service, cache, concurrency, pre-cache liveness, and persistence.
   - `services/api/tests/test_matching_router.py`: HTTP endpoint tests.
   - `services/api/tests/test_matching_evals.py`: 15 golden regression scenarios.

### Acceptance Criteria:
- `POST /api/v1/jobs/{jobId}/match` returns complete, typed `MatchResponse` in < 50ms (p95);
- Unconfirmed facts are structurally excluded from matching;
- Hard blockers reliably force `overall_score = 0` and `eligibility = INELIGIBLE`;
- Undisclosed job attributes (e.g. salary) never trigger hard blockers;
- Concurrent evaluations for the same version idempotently resolve with verified result digest equality;
- Pre-cache guard prevents serving stale cache for closed or unauthorized jobs;
- 15 golden eval scenarios and all architectural invariant test suites pass with 100% determinism;
- All local and remote CI gates pass 8/8 green.
