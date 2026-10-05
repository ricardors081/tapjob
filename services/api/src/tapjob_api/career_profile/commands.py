"""Validated domain commands for the channel-first use-case layer (docs/adr/0010-career-profile-
truth-store.md "channel-first design", round-3 independent review "BLOCKER 1"). `service.py` itself
REQUIRES these typed, validated commands -- never a plain `dict`/loosely-typed kwargs relying on a
caller (the HTTP router today, a future WhatsApp/Agent adapter tomorrow) to have already applied the
same rules. `tapjob_api.career_profile.schemas.UpdateCareerProfileRequest` is a thin HTTP-specific
subclass of `CareerProfilePatchCommand` below (adding `expectedProfileVersion` and camelCase
aliasing) -- composition/inheritance, never a second, independently-maintained copy of these
validators.
"""

from pydantic import BaseModel, Field, field_validator, model_validator

from tapjob_api.career_profile.fact_values import (
    normalize_description,
    normalize_label,
    normalize_label_collapsing_spaces,
)

HEADLINE_MAX_LENGTH = 200
SUMMARY_MAX_LENGTH = 4000
MAX_TARGET_ROLES = 20
TARGET_ROLE_MAX_LENGTH = 100

_MUTABLE_FIELD_NAMES = frozenset({"headline", "summary", "target_roles"})


class CareerProfilePatchCommand(BaseModel):
    """`headline`/`summary`/`target_roles` absent vs. explicitly `None` is tracked via
    `model_fields_set`, exactly as `UpdateCareerProfileRequest` already did at the HTTP layer --
    `service.update_career_profile` reads `command.model_fields_set` itself now, so this
    distinction is a domain concept, not an HTTP one."""

    model_config = {"populate_by_name": True}

    headline: str | None = Field(default=None, max_length=HEADLINE_MAX_LENGTH)
    summary: str | None = Field(default=None, max_length=SUMMARY_MAX_LENGTH)
    target_roles: list[str] | None = Field(default=None, max_length=MAX_TARGET_ROLES)

    @field_validator("headline", mode="after")
    @classmethod
    def _normalize_headline(cls, value: str | None) -> str | None:
        return normalize_label(value, field_name="headline")

    @field_validator("summary", mode="after")
    @classmethod
    def _normalize_summary(cls, value: str | None) -> str | None:
        return normalize_description(value, field_name="summary")

    @field_validator("target_roles", mode="after")
    @classmethod
    def _normalize_target_roles(cls, value: list[str] | None) -> list[str] | None:
        # A normalized (trimmed/collapsed, non-blank, control-character-free), deduplicated
        # (exact/case-insensitive, preserving the FIRST spelling and order) list of role titles --
        # `max_length` above only bounds the list's length, not each item's size or content
        # (docs/adr/0010-...md "value JSON safety", "truth store non-blank/normalization policy").
        # Never a semantic occupation taxonomy: "JS" and "JavaScript" remain distinct roles, exactly
        # like skills. Control-character rejection happens BEFORE whitespace collapsing (round-2
        # independent review "BLOCKER 5" -- see `normalize_label_collapsing_spaces`).
        if value is None:
            return None
        normalized: list[str] = []
        seen_casefolded: set[str] = set()
        for role in value:
            collapsed = normalize_label_collapsing_spaces(role, field_name="target role")
            if len(collapsed) > TARGET_ROLE_MAX_LENGTH:
                raise ValueError(
                    f"each target role must be at most {TARGET_ROLE_MAX_LENGTH} characters"
                )
            key = collapsed.casefold()
            if key in seen_casefolded:
                continue
            seen_casefolded.add(key)
            normalized.append(collapsed)
        return normalized

    @model_validator(mode="after")
    def _require_at_least_one_mutable_field(self) -> "CareerProfilePatchCommand":
        # `profile_version` represents a change in professional truth -- a command that sets no
        # mutable field at all is not a logical mutation and must never create a `CareerProfile`
        # row, bump the version, or produce a success audit event (docs/adr/0010-...md "profile
        # patch", round-1 independent review "empty PATCH"). Intersecting with the fixed set of
        # known mutable field names (rather than subtracting some OTHER field, e.g. a subclass's
        # own `expectedProfileVersion`) keeps this correct regardless of what extra fields a
        # subclass like `UpdateCareerProfileRequest` adds.
        if not (self.model_fields_set & _MUTABLE_FIELD_NAMES):
            raise ValueError("at least one of headline/summary/target_roles must be provided")
        return self
