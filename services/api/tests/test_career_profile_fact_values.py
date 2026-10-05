"""Unit tests for typed `ProfileFact` value models (docs/adr/0010-career-profile-truth-store.md
"typed value models", "no invented date precision", "link safety", "money"). Pure Pydantic
validation -- no database.
"""

import pytest
from pydantic import ValidationError

from tapjob_api.career_profile.fact_values import (
    CertificationFactValue,
    EducationFactValue,
    EmploymentType,
    ExperienceFactValue,
    LanguageFactValue,
    LinkFactValue,
    LocationPreferenceValue,
    PreferenceFactValue,
    ProjectFactValue,
    SalaryPeriod,
    SalaryPreferenceValue,
    SkillFactValue,
    WorkAuthorizationFactValue,
    WorkplaceType,
    WorkplaceTypePreferenceValue,
    YearMonth,
)

# -- YearMonth / no invented precision ------------------------------------------------------------


def test_year_month_accepts_year_only() -> None:
    value = YearMonth(year=2024)
    assert value.month is None


def test_year_month_rejects_month_out_of_range() -> None:
    with pytest.raises(ValidationError):
        YearMonth(year=2024, month=13)


def test_year_month_rejects_year_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        YearMonth(year=1800)


def test_start_year_only_december_vs_end_same_year_no_month_is_accepted() -> None:
    # round-2 independent review "BLOCKER 3": end=2024 means "year only known" -- NEVER treated as
    # January, so this must NOT register as "start after end" even though start has month=12.
    fact = ExperienceFactValue(
        employer="Acme",
        title="Engineer",
        start=YearMonth(year=2024, month=12),
        end=YearMonth(year=2024),
    )
    assert fact.end is not None and fact.end.month is None


def test_start_year_only_vs_end_same_year_january_is_accepted() -> None:
    # start=2024 (year only) vs end=2024-01 -- the user never asserted start was in/after January,
    # so this is NOT rejected either (the ordering is undecidable given the known precision).
    fact = ExperienceFactValue(
        employer="Acme",
        title="Engineer",
        start=YearMonth(year=2024),
        end=YearMonth(year=2024, month=1),
    )
    assert fact.start.month is None


def test_start_year_after_end_year_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme", title="Engineer", start=YearMonth(year=2025), end=YearMonth(year=2024)
        )


def test_start_and_end_both_known_months_same_year_out_of_order_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme",
            title="Engineer",
            start=YearMonth(year=2024, month=12),
            end=YearMonth(year=2024, month=11),
        )


def test_same_year_no_months_known_at_all_is_accepted() -> None:
    fact = ExperienceFactValue(
        employer="Acme", title="Engineer", start=YearMonth(year=2024), end=YearMonth(year=2024)
    )
    assert fact.start.month is None
    assert fact.end is not None and fact.end.month is None


# -- Experience -------------------------------------------------------------------------------


def test_experience_accepts_year_only_start() -> None:
    fact = ExperienceFactValue(employer="Acme", title="Engineer", start=YearMonth(year=2022))
    assert fact.start.month is None


def test_experience_rejects_start_after_end() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme",
            title="Engineer",
            start=YearMonth(year=2024),
            end=YearMonth(year=2022),
        )


def test_experience_rejects_current_with_an_end_date() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme",
            title="Engineer",
            start=YearMonth(year=2022),
            end=YearMonth(year=2023),
            current=True,
        )


def test_experience_accepts_current_without_an_end_date() -> None:
    fact = ExperienceFactValue(
        employer="Acme", title="Engineer", start=YearMonth(year=2022), current=True
    )
    assert fact.end is None


def test_experience_bounds_each_achievement_length() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme",
            title="Engineer",
            start=YearMonth(year=2022),
            achievements=["x" * 501],
        )


def test_experience_bounds_achievement_count() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme",
            title="Engineer",
            start=YearMonth(year=2022),
            achievements=["ok"] * 21,
        )


def test_experience_accepts_typed_employment_type() -> None:
    fact = ExperienceFactValue(
        employer="Acme",
        title="Engineer",
        start=YearMonth(year=2022),
        employment_type=EmploymentType.CONTRACT,
    )
    assert fact.employment_type is EmploymentType.CONTRACT


def test_experience_rejects_empty_employer() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(employer="", title="Engineer", start=YearMonth(year=2022))


def test_experience_rejects_whitespace_only_employer() -> None:
    # `min_length=1` alone would pass a whitespace-only string -- the non-blank/normalization
    # policy is what actually catches this (docs/adr/0010-...md, round-1 independent review
    # "BLOCKER 4").
    with pytest.raises(ValidationError):
        ExperienceFactValue(employer="   ", title="Engineer", start=YearMonth(year=2022))


def test_experience_trims_employer_and_title() -> None:
    fact = ExperienceFactValue(
        employer="  Acme  ", title="  Engineer  ", start=YearMonth(year=2022)
    )
    assert fact.employer == "Acme"
    assert fact.title == "Engineer"


def test_experience_rejects_control_characters_in_title() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(employer="Acme", title="Eng\x00ineer", start=YearMonth(year=2022))


def test_experience_preserves_legitimate_unicode_in_employer() -> None:
    fact = ExperienceFactValue(
        employer="Café São Paulo 🤝", title="Engenheira", start=YearMonth(year=2022)
    )
    assert fact.employer == "Café São Paulo 🤝"


def test_experience_description_preserves_internal_newlines() -> None:
    fact = ExperienceFactValue(
        employer="Acme",
        title="Engineer",
        start=YearMonth(year=2022),
        description="Line one.\nLine two.",
    )
    assert fact.description == "Line one.\nLine two."


def test_experience_rejects_whitespace_only_description() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme", title="Engineer", start=YearMonth(year=2022), description="   "
        )


def test_experience_rejects_whitespace_only_achievement() -> None:
    with pytest.raises(ValidationError):
        ExperienceFactValue(
            employer="Acme",
            title="Engineer",
            start=YearMonth(year=2022),
            achievements=["Shipped feature X", "   "],
        )


# -- Education --------------------------------------------------------------------------------


def test_education_rejects_start_after_end() -> None:
    with pytest.raises(ValidationError):
        EducationFactValue(
            institution="State University",
            start=YearMonth(year=2024),
            end=YearMonth(year=2020),
        )


def test_education_allows_no_dates_at_all() -> None:
    fact = EducationFactValue(institution="State University")
    assert fact.start is None
    assert fact.end is None


def test_education_rejects_whitespace_only_institution() -> None:
    with pytest.raises(ValidationError):
        EducationFactValue(institution="   ")


def test_education_trims_institution() -> None:
    fact = EducationFactValue(institution="  State University  ")
    assert fact.institution == "State University"


# -- Skill --------------------------------------------------------------------------------------


def test_skill_trims_and_collapses_whitespace() -> None:
    fact = SkillFactValue(name="  Python   Programming  ")
    assert fact.name == "Python Programming"


def test_skill_never_merges_similar_names_semantically() -> None:
    # "JS" and "JavaScript" are two different skills -- this model does zero semantic merging; it
    # only normalizes whitespace (docs/adr/0010-...md "duplicates").
    assert SkillFactValue(name="JS").name != SkillFactValue(name="JavaScript").name


def test_skill_proficiency_is_optional_and_never_defaulted() -> None:
    fact = SkillFactValue(name="Python")
    assert fact.proficiency is None


def test_skill_rejects_unreasonable_years_experience() -> None:
    with pytest.raises(ValidationError):
        SkillFactValue(name="Python", years_experience=200)


def test_skill_rejects_a_whitespace_only_name() -> None:
    # Round-1 independent review "SKILL VALIDATOR BUG": `min_length=1` passed on the PRE-collapse
    # string, so "   " satisfied it and THEN collapsed to "" -- a blank skill silently accepted.
    # Fixed: the collapsed result is what must be non-blank.
    with pytest.raises(ValidationError):
        SkillFactValue(name="   ")


def test_skill_rejects_a_name_that_is_only_internal_whitespace() -> None:
    with pytest.raises(ValidationError):
        SkillFactValue(name=" \t \n ")


def test_skill_rejects_control_characters() -> None:
    with pytest.raises(ValidationError):
        SkillFactValue(name="Python\x00")


def test_skill_rejects_an_internal_newline_not_silently_collapsed_away() -> None:
    # round-2 independent review "BLOCKER 5": the previous `" ".join(value.split())` idiom
    # collapses ANY whitespace, including a literal newline, into a regular space BEFORE any
    # control-character check ever ran -- so "Python\nDeveloper" silently passed as "Python
    # Developer" instead of being rejected. Control-character rejection must happen first.
    with pytest.raises(ValidationError):
        SkillFactValue(name="Python\nDeveloper")


def test_skill_rejects_an_internal_tab_not_silently_collapsed_away() -> None:
    with pytest.raises(ValidationError):
        SkillFactValue(name="Python\tDeveloper")


def test_skill_collapses_multiple_regular_spaces_but_preserves_unicode() -> None:
    fact = SkillFactValue(name="  Atendimento   ao   cliente 🤝  ")
    assert fact.name == "Atendimento ao cliente 🤝"


# -- Certification ----------------------------------------------------------------------------


def test_certification_rejects_issued_after_expires() -> None:
    with pytest.raises(ValidationError):
        CertificationFactValue(
            name="AWS SAA",
            issuer="AWS",
            issued=YearMonth(year=2024),
            expires=YearMonth(year=2020),
        )


def test_certification_rejects_unsafe_url_scheme() -> None:
    with pytest.raises(ValidationError):
        CertificationFactValue.model_validate(
            {"name": "AWS SAA", "issuer": "AWS", "credential_url": "javascript:alert(1)"}
        )


def test_certification_accepts_a_real_https_url() -> None:
    fact = CertificationFactValue.model_validate(
        {"name": "AWS SAA", "issuer": "AWS", "credential_url": "https://aws.amazon.com/verify"}
    )
    assert str(fact.credential_url).startswith("https://")


# -- Language ---------------------------------------------------------------------------------


def test_language_never_forces_a_cefr_scale() -> None:
    # Free-form bounded text -- never converted into a scale the user never stated
    # (docs/adr/0010-...md "no invented proficiency scale").
    fact = LanguageFactValue(language="Portuguese", proficiency="fluent")
    assert fact.proficiency == "fluent"


def test_language_proficiency_is_optional() -> None:
    fact = LanguageFactValue(language="Portuguese")
    assert fact.proficiency is None


# -- Project ------------------------------------------------------------------------------------


def test_project_rejects_start_after_end() -> None:
    with pytest.raises(ValidationError):
        ProjectFactValue(name="Side project", start=YearMonth(year=2024), end=YearMonth(year=2022))


def test_project_bounds_technology_list() -> None:
    with pytest.raises(ValidationError):
        ProjectFactValue(name="Side project", technologies=["x"] * 31)


def test_project_bounds_each_technology_string_length() -> None:
    with pytest.raises(ValidationError):
        ProjectFactValue(name="Side project", technologies=["x" * 101])


def test_project_rejects_a_whitespace_only_technology() -> None:
    with pytest.raises(ValidationError):
        ProjectFactValue(name="Side project", technologies=["Python", "   "])


def test_project_rejects_whitespace_only_name() -> None:
    with pytest.raises(ValidationError):
        ProjectFactValue(name="   ")


def test_project_rejects_unsafe_url_scheme() -> None:
    with pytest.raises(ValidationError):
        ProjectFactValue.model_validate({"name": "Side project", "url": "data:text/html,<script>"})


# -- Link ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd"]
)
def test_link_rejects_dangerous_schemes(scheme: str) -> None:
    with pytest.raises(ValidationError):
        LinkFactValue.model_validate({"link_type": "portfolio", "url": scheme})


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com"])
def test_link_accepts_http_and_https(url: str) -> None:
    fact = LinkFactValue.model_validate({"link_type": "portfolio", "url": url})
    assert str(fact.url).startswith(("http://", "https://"))


def test_link_requires_a_url() -> None:
    with pytest.raises(ValidationError):
        LinkFactValue.model_validate({"link_type": "portfolio"})


# -- Preferences (salary is NEVER a float) -----------------------------------------------------


def test_salary_preference_uses_integer_minor_units_not_float() -> None:
    fact = SalaryPreferenceValue(
        minor_unit_min=500000, minor_unit_max=800000, currency="BRL", period=SalaryPeriod.MONTHLY
    )
    assert isinstance(fact.minor_unit_min, int)


def test_salary_preference_rejects_min_greater_than_max() -> None:
    with pytest.raises(ValidationError):
        SalaryPreferenceValue(
            minor_unit_min=900000,
            minor_unit_max=500000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
        )


def test_salary_preference_rejects_a_lowercase_currency_code() -> None:
    with pytest.raises(ValidationError):
        SalaryPreferenceValue(currency="brl", period=SalaryPeriod.MONTHLY)


def test_salary_preference_requires_at_least_one_amount_bound() -> None:
    # round-2 independent review "BLOCKER 4": currency/period alone express no actual preference.
    with pytest.raises(ValidationError):
        SalaryPreferenceValue(currency="BRL", period=SalaryPeriod.MONTHLY)


def test_salary_preference_accepts_min_only() -> None:
    fact = SalaryPreferenceValue(minor_unit_min=500000, currency="BRL", period=SalaryPeriod.MONTHLY)
    assert fact.minor_unit_max is None


def test_salary_preference_accepts_max_only() -> None:
    fact = SalaryPreferenceValue(minor_unit_max=800000, currency="BRL", period=SalaryPeriod.MONTHLY)
    assert fact.minor_unit_min is None


def test_salary_preference_currency_is_format_only_not_a_real_iso_4217_registry_check() -> None:
    # Factual cleanup (round-1 independent review): `currency` validates the SHAPE of a currency
    # code (three uppercase letters) only -- it is NOT checked against the real ISO 4217 registry,
    # and a well-formed but non-existent code like "ABC" is accepted. Implementing full registry
    # membership would mean a new dependency this WP has no concrete need for
    # (docs/adr/0010-...md "currency validation policy"); this test exists so that fact is explicit
    # and never silently reinterpreted as "ISO 4217 validated" by a future reader.
    fact = SalaryPreferenceValue(minor_unit_min=500000, currency="ABC", period=SalaryPeriod.MONTHLY)
    assert fact.currency == "ABC"


def test_location_preference_never_has_coordinates() -> None:
    fact = LocationPreferenceValue(
        city="Cuiabá", region="MT", country="BR", workplace_types=[WorkplaceType.REMOTE]
    )
    assert not hasattr(fact, "latitude")
    assert not hasattr(fact, "longitude")


def test_location_preference_rejects_all_fields_empty() -> None:
    # round-2 independent review "BLOCKER 4": preferenceKind="location" alone expresses nothing.
    with pytest.raises(ValidationError):
        LocationPreferenceValue()


def test_location_preference_accepts_workplace_types_alone() -> None:
    fact = LocationPreferenceValue(workplace_types=[WorkplaceType.REMOTE])
    assert fact.country is None


def test_location_preference_accepts_city_alone() -> None:
    fact = LocationPreferenceValue(city="Cuiabá")
    assert fact.workplace_types == []


def test_preference_fact_value_discriminates_by_preference_kind() -> None:
    # fact_values.py is the internal domain vocabulary (snake_case); schemas.py is the separate
    # HTTP/wire layer that adds camelCase aliases on top of it.
    fact = PreferenceFactValue.model_validate(
        {"preference": {"preference_kind": "workplace_type", "workplace_types": ["remote"]}}
    )
    assert isinstance(fact.preference, WorkplaceTypePreferenceValue)


# -- Work authorization (sensitive/legal) -------------------------------------------------------


def test_work_authorization_answer_is_bounded_text_not_a_bare_boolean() -> None:
    fact = WorkAuthorizationFactValue(
        jurisdiction="US", question_key="requires_sponsorship", answer="no"
    )
    assert fact.answer == "no"


def test_work_authorization_rejects_an_overlong_answer() -> None:
    with pytest.raises(ValidationError):
        WorkAuthorizationFactValue(
            jurisdiction="US", question_key="requires_sponsorship", answer="x" * 201
        )


def test_work_authorization_question_key_is_bounded_and_pattern_restricted() -> None:
    with pytest.raises(ValidationError):
        WorkAuthorizationFactValue(
            jurisdiction="US", question_key="Requires Sponsorship!", answer="no"
        )
