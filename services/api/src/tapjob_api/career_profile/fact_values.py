"""Typed `ProfileFact.value_json` schemas (docs/adr/0010-career-profile-truth-store.md "typed
value models"). No fact type ever stores `{"type": "...", "value": Any}` -- every shape is a
validated Pydantic model with explicit bounds, never arbitrary deeply nested JSON
(docs/adr/0010-...md "value JSON safety"). A separate module from `schemas.py` (the HTTP request/
response DTOs) because these shapes are also the service layer's own internal vocabulary, not just
wire contracts.

Every multi-word field carries an explicit camelCase `Field(alias=...)` -- the same convention
`schemas.py`/`tapjob_api.identity.schemas` use -- since these models are also parsed directly from
the wire as `AddProfileFactRequest.value`/`ReplaceProfileFactRequest.value`
(`docs/ENGINEERING_STANDARDS.md` "CamelCase wire format"). `router.py` persists `value_json` via
`model_dump(mode="json", by_alias=True)`, so a stored/returned fact's `value` is camelCase too --
the same shape a client sent it in.
"""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, ValidationInfo, field_validator, model_validator

# -- bounds (docs/ENGINEERING_STANDARDS.md §2: named, not scattered magic numbers) ----------------

_SHORT_TEXT = 200
_LABEL_TEXT = 100
_LONG_TEXT = 2000
_BULLET_TEXT = 500
_MAX_BULLETS = 20
_MAX_TECHNOLOGIES = 30
_MIN_YEAR = 1950
_MAX_YEAR = 2100

# -- non-blank / normalization policy (docs/adr/0010-...md "truth store non-blank/normalization
# policy"): the Professional Truth Store must never accept a semantically empty claim. Every plain
# free-text field is trimmed and rejected if blank after trimming -- NEVER silently munged into an
# empty string that would then satisfy a bare `min_length=1`. Legitimate Unicode (letters, digits,
# punctuation, diacritics, emoji) is always preserved; only ASCII control characters are rejected.
# This is never a semantic taxonomy: "JS" and "JavaScript" remain distinct skills -- trimming and
# control-char rejection is the full extent of this policy. ---------------------------------------

_CONTROL_CHARS_ANY = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_CHARS_NO_NEWLINE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _require_non_blank(trimmed: str, *, field_name: str, pattern: re.Pattern[str]) -> str:
    if not trimmed:
        raise ValueError(f"{field_name} must not be blank")
    if pattern.search(trimmed):
        raise ValueError(f"{field_name} must not contain control characters")
    return trimmed


def normalize_label(value: str | None, *, field_name: str) -> str | None:
    """Single-line identifier/label text: trim, reject blank-after-trim, reject any control
    character (including newlines -- a label is never multi-line)."""
    if value is None:
        return None
    return _require_non_blank(value.strip(), field_name=field_name, pattern=_CONTROL_CHARS_ANY)


def normalize_description(value: str | None, *, field_name: str) -> str | None:
    """Multi-line descriptive text: same trim/reject-blank policy as `normalize_label`, but
    legitimate internal newlines are preserved -- only OTHER control characters are rejected."""
    if value is None:
        return None
    return _require_non_blank(
        value.strip(), field_name=field_name, pattern=_CONTROL_CHARS_NO_NEWLINE
    )


def normalize_label_list_item(value: str, *, field_name: str) -> str:
    return _require_non_blank(value.strip(), field_name=field_name, pattern=_CONTROL_CHARS_ANY)


_MULTI_SPACE = re.compile(r" {2,}")


def normalize_label_collapsing_spaces(value: str, *, field_name: str) -> str:
    """Same policy as `normalize_label`, but also collapses runs of regular spaces into one -- for
    fields where internal whitespace normalization is wanted (`SkillFactValue.name`,
    `UpdateCareerProfileRequest.target_roles`). Control-character rejection happens on the TRIMMED
    string BEFORE any collapsing (round-2 independent review "BLOCKER 5": the previous
    `" ".join(value.split())` idiom collapses on ANY whitespace, including a literal newline/tab --
    which silently replaced it with a regular space before a control-character check ever saw it,
    so "Python\\nDeveloper" passed as "Python Developer" instead of being rejected)."""
    trimmed = value.strip()
    if _CONTROL_CHARS_ANY.search(trimmed):
        raise ValueError(f"{field_name} must not contain control characters")
    collapsed = _MULTI_SPACE.sub(" ", trimmed)
    if not collapsed:
        raise ValueError(f"{field_name} must not be blank")
    return collapsed


class YearMonth(BaseModel):
    """Year/month precision only -- never a fabricated day
    (docs/adr/0010-...md "no invented date precision"). `month` absent means the user only knows
    the year -- NEVER treated as January (or any other specific month) for comparison purposes
    (round-2 independent review "BLOCKER 3": a prior `ordinal()` helper collapsed an unknown month
    to January, which made e.g. `start=2024-12`/`end=2024` falsely register as "start after end" --
    the user never asserted a January end, only a year)."""

    model_config = {"populate_by_name": True}

    year: int = Field(ge=_MIN_YEAR, le=_MAX_YEAR)
    month: int | None = Field(default=None, ge=1, le=12)


def _check_start_before_end(start: YearMonth | None, end: YearMonth | None) -> None:
    """Conservative partial-date ordering (docs/adr/0010-...md "no invented date precision",
    round-2 independent review "BLOCKER 3"): only rejects when the two dates are UNAMBIGUOUSLY out
    of order given the precision actually known. A year-only date's ordering relative to a same-year
    date with a known month is deliberately left UNDECIDABLE -- never invalid, never assumed valid
    via a fabricated month -- since the user never asserted enough precision to know either way."""
    if start is None or end is None:
        return
    if start.year > end.year:
        raise ValueError("start must not be after end")
    if start.year < end.year:
        return
    # Same year: only comparable when BOTH months are actually known.
    if start.month is not None and end.month is not None and start.month > end.month:
        raise ValueError("start must not be after end")


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"


class ExperienceFactValue(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["experience"] = "experience"
    employer: str = Field(min_length=1, max_length=_SHORT_TEXT)
    title: str = Field(min_length=1, max_length=_SHORT_TEXT)
    start: YearMonth
    end: YearMonth | None = None
    current: bool = False
    location: str | None = Field(default=None, max_length=_SHORT_TEXT)
    description: str | None = Field(default=None, max_length=_LONG_TEXT)
    achievements: list[str] = Field(default_factory=list, max_length=_MAX_BULLETS)
    employment_type: EmploymentType | None = Field(default=None, alias="employmentType")

    @field_validator("employer", "title", "location", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")

    @field_validator("description", mode="after")
    @classmethod
    def _normalize_description_field(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_description(value, field_name=info.field_name or "value")

    @field_validator("achievements", mode="after")
    @classmethod
    def _bound_achievement_length(cls, value: list[str]) -> list[str]:
        normalized = [normalize_label_list_item(item, field_name="achievement") for item in value]
        for item in normalized:
            if len(item) > _BULLET_TEXT:
                raise ValueError(f"each achievement must be at most {_BULLET_TEXT} characters")
        return normalized

    @model_validator(mode="after")
    def _validate_dates(self) -> "ExperienceFactValue":
        if self.current and self.end is not None:
            raise ValueError("a current experience must not have an end date")
        _check_start_before_end(self.start, self.end)
        return self


class EducationStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class EducationFactValue(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["education"] = "education"
    institution: str = Field(min_length=1, max_length=_SHORT_TEXT)
    degree: str | None = Field(default=None, max_length=_SHORT_TEXT)
    field_of_study: str | None = Field(default=None, max_length=_SHORT_TEXT, alias="fieldOfStudy")
    start: YearMonth | None = None
    end: YearMonth | None = None
    status: EducationStatus | None = None
    description: str | None = Field(default=None, max_length=_LONG_TEXT)

    @field_validator("institution", "degree", "field_of_study", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")

    @field_validator("description", mode="after")
    @classmethod
    def _normalize_description_field(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_description(value, field_name=info.field_name or "value")

    @model_validator(mode="after")
    def _validate_dates(self) -> "EducationFactValue":
        _check_start_before_end(self.start, self.end)
        return self


class SkillFactValue(BaseModel):
    """No proficiency is ever inferred -- `proficiency` only exists when the user actually
    supplied one (docs/adr/0010-...md "no inferred proficiency")."""

    model_config = {"populate_by_name": True}

    kind: Literal["skill"] = "skill"
    name: str = Field(min_length=1, max_length=_LABEL_TEXT)
    proficiency: str | None = Field(default=None, max_length=32)
    years_experience: float | None = Field(default=None, ge=0, le=80, alias="yearsExperience")

    @field_validator("name", mode="after")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        # Canonicalization only (trim + collapse internal whitespace for duplicate-key comparison
        # elsewhere) -- NEVER a semantic merge; "Java" and "JavaScript" remain distinct skills
        # (docs/adr/0010-...md "duplicates"). Control characters (including newlines/tabs) are
        # rejected BEFORE whitespace is collapsed, not after (round-2 independent review
        # "BLOCKER 5" -- see `normalize_label_collapsing_spaces`'s own docstring for why order
        # matters here).
        return normalize_label_collapsing_spaces(value, field_name="name")

    @field_validator("proficiency", mode="after")
    @classmethod
    def _normalize_proficiency(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")


class CertificationFactValue(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["certification"] = "certification"
    name: str = Field(min_length=1, max_length=_SHORT_TEXT)
    issuer: str = Field(min_length=1, max_length=_SHORT_TEXT)
    issued: YearMonth | None = None
    expires: YearMonth | None = None
    credential_id: str | None = Field(default=None, max_length=_SHORT_TEXT, alias="credentialId")
    credential_url: HttpUrl | None = Field(default=None, alias="credentialUrl")

    @field_validator("name", "issuer", "credential_id", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")

    @model_validator(mode="after")
    def _validate_dates(self) -> "CertificationFactValue":
        _check_start_before_end(self.issued, self.expires)
        return self


class LanguageFactValue(BaseModel):
    """`proficiency` is free-form, bounded text -- never forced into a CEFR scale the user never
    stated (docs/adr/0010-...md "no invented proficiency scale")."""

    model_config = {"populate_by_name": True}

    kind: Literal["language"] = "language"
    language: str = Field(min_length=1, max_length=_LABEL_TEXT)
    proficiency: str | None = Field(default=None, max_length=32)

    @field_validator("language", "proficiency", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")


class ProjectFactValue(BaseModel):
    model_config = {"populate_by_name": True}

    kind: Literal["project"] = "project"
    name: str = Field(min_length=1, max_length=_SHORT_TEXT)
    description: str | None = Field(default=None, max_length=_LONG_TEXT)
    role: str | None = Field(default=None, max_length=_SHORT_TEXT)
    start: YearMonth | None = None
    end: YearMonth | None = None
    url: HttpUrl | None = None
    technologies: list[str] = Field(default_factory=list, max_length=_MAX_TECHNOLOGIES)

    @field_validator("name", "role", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")

    @field_validator("description", mode="after")
    @classmethod
    def _normalize_description_field(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_description(value, field_name=info.field_name or "value")

    @field_validator("technologies", mode="after")
    @classmethod
    def _bound_each_technology(cls, value: list[str]) -> list[str]:
        # `max_length` on the field only bounds the LIST's length -- without this, each individual
        # technology string would be unbounded, a payload-bomb vector (docs/adr/0010-...md "value
        # JSON safety").
        normalized = [normalize_label_list_item(item, field_name="technology") for item in value]
        for item in normalized:
            if len(item) > _LABEL_TEXT:
                raise ValueError(f"each technology must be at most {_LABEL_TEXT} characters")
        return normalized

    @model_validator(mode="after")
    def _validate_dates(self) -> "ProjectFactValue":
        _check_start_before_end(self.start, self.end)
        return self


class LinkFactValue(BaseModel):
    """Only `http`/`https` ever validates -- `pydantic.HttpUrl` structurally rejects
    `javascript:`/`data:`/`file:` and every other non-http(s) scheme
    (docs/adr/0010-...md "link safety"). No URL is ever fetched by this WP."""

    model_config = {"populate_by_name": True}

    kind: Literal["link"] = "link"
    link_type: str = Field(min_length=1, max_length=32, alias="linkType")
    label: str | None = Field(default=None, max_length=_LABEL_TEXT)
    url: HttpUrl

    @field_validator("link_type", "label", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")


class WorkplaceType(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class SalaryPeriod(StrEnum):
    HOURLY = "hourly"
    MONTHLY = "monthly"
    ANNUAL = "annual"


class SalaryPreferenceValue(BaseModel):
    """Money is NEVER a float (docs/ENGINEERING_STANDARDS.md §11) -- integer minor units plus a
    separate currency code, exactly `amountMinor`/`currency`'s documented shape. No currency
    conversion ever happens here.

    `currency` validates SHAPE only (three uppercase letters) -- NOT real ISO 4217 registry
    membership. A well-formed but non-existent code (e.g. "ABC") is accepted (round-1 independent
    review, "currency validation policy": implementing full registry validation would require a new
    dependency this WP has no concrete need for; this is a deliberate, documented choice, not an
    oversight)."""

    model_config = {"populate_by_name": True}

    preference_kind: Literal["salary"] = Field(default="salary", alias="preferenceKind")
    minor_unit_min: int | None = Field(default=None, ge=0, le=10_000_000_00, alias="minorUnitMin")
    minor_unit_max: int | None = Field(default=None, ge=0, le=10_000_000_00, alias="minorUnitMax")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: SalaryPeriod

    @model_validator(mode="after")
    def _validate_range(self) -> "SalaryPreferenceValue":
        if (
            self.minor_unit_min is not None
            and self.minor_unit_max is not None
            and self.minor_unit_min > self.minor_unit_max
        ):
            raise ValueError("minor_unit_min must not be greater than minor_unit_max")
        if self.minor_unit_min is None and self.minor_unit_max is None:
            # `currency`/`period` alone express no actual preference (round-2 independent review
            # "BLOCKER 4") -- at least one bound must be given. No product semantics for a
            # "currency/period only" preference have been canonically decided, so none is invented
            # here.
            raise ValueError("at least one of minor_unit_min/minor_unit_max must be provided")
        return self


class LocationPreferenceValue(BaseModel):
    """No geocoding, no coordinates (docs/adr/0010-...md "no geocoding in WP1.2") -- plain,
    bounded, user-supplied location text."""

    model_config = {"populate_by_name": True}

    preference_kind: Literal["location"] = Field(default="location", alias="preferenceKind")
    country: str | None = Field(default=None, max_length=_LABEL_TEXT)
    region: str | None = Field(default=None, max_length=_LABEL_TEXT)
    city: str | None = Field(default=None, max_length=_LABEL_TEXT)
    workplace_types: list[WorkplaceType] = Field(
        default_factory=list, max_length=3, alias="workplaceTypes"
    )

    @field_validator("country", "region", "city", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")

    @model_validator(mode="after")
    def _require_at_least_one_meaningful_field(self) -> "LocationPreferenceValue":
        # `preferenceKind: "location"` alone, with every field empty, expresses no actual
        # preference (round-2 independent review "BLOCKER 4").
        if not (self.country or self.region or self.city or self.workplace_types):
            raise ValueError("at least one of country/region/city/workplaceTypes must be provided")
        return self


class WorkplaceTypePreferenceValue(BaseModel):
    model_config = {"populate_by_name": True}

    preference_kind: Literal["workplace_type"] = Field(
        default="workplace_type", alias="preferenceKind"
    )
    workplace_types: list[WorkplaceType] = Field(min_length=1, max_length=3, alias="workplaceTypes")


class EmploymentTypePreferenceValue(BaseModel):
    model_config = {"populate_by_name": True}

    preference_kind: Literal["employment_type"] = Field(
        default="employment_type", alias="preferenceKind"
    )
    employment_types: list[EmploymentType] = Field(
        min_length=1, max_length=5, alias="employmentTypes"
    )


PreferenceValue = Annotated[
    SalaryPreferenceValue
    | LocationPreferenceValue
    | WorkplaceTypePreferenceValue
    | EmploymentTypePreferenceValue,
    Field(discriminator="preference_kind"),
]


class PreferenceFactValue(BaseModel):
    """`fact_type = preference`'s own payload is itself a discriminated union on
    `preference_kind` -- one `FactType` member deliberately covers several preference shapes
    (docs/adr/0010-...md "preferences"), since the canonical `fact_type` vocabulary
    (docs/IMPLEMENTATION_PLAN.md) names `preference` once, not once per kind."""

    model_config = {"populate_by_name": True}

    kind: Literal["preference"] = "preference"
    preference: PreferenceValue


class WorkAuthorizationFactValue(BaseModel):
    """Sensitive/legal (docs/SECURITY_PRIVACY.md §5, docs/adr/0010-...md "work authorization"):
    `answer` is a bounded string, never a bare unscoped boolean -- it only has meaning paired with
    `jurisdiction`/`question_key`, and this WP invents no global legal-question catalog. The
    server never infers or defaults this value; it is only ever written by an explicit, confirmed
    user action (`service.py`'s `source_kind` enforcement)."""

    model_config = {"populate_by_name": True}

    kind: Literal["work_authorization"] = "work_authorization"
    jurisdiction: str = Field(min_length=1, max_length=8)
    question_key: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$", alias="questionKey"
    )
    answer: str = Field(min_length=1, max_length=_SHORT_TEXT)

    @field_validator("jurisdiction", "answer", mode="after")
    @classmethod
    def _normalize_labels(cls, value: str | None, info: ValidationInfo) -> str | None:
        return normalize_label(value, field_name=info.field_name or "value")


FactValue = Annotated[
    ExperienceFactValue
    | EducationFactValue
    | SkillFactValue
    | CertificationFactValue
    | LanguageFactValue
    | ProjectFactValue
    | LinkFactValue
    | PreferenceFactValue
    | WorkAuthorizationFactValue,
    Field(discriminator="kind"),
]

FACT_VALUE_MODELS: tuple[type[BaseModel], ...] = (
    ExperienceFactValue,
    EducationFactValue,
    SkillFactValue,
    CertificationFactValue,
    LanguageFactValue,
    ProjectFactValue,
    LinkFactValue,
    PreferenceFactValue,
    WorkAuthorizationFactValue,
)
