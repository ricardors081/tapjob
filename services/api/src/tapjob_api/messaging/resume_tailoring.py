"""Resume tailoring layer grounded in canonical Truth Store (Finding 9, AGENTS.md §6).

CRITICAL GROUNDING PRINCIPLES:
- Every claim in tailored resume MUST be grounded in facts from get_truth_snapshot().
- Zero LLM hallucinations; deterministic fallback guaranteed.
- Strict post-generation claim-level grounding validation.
- ready_for_submission is FALSE unless artifact is proven grounded.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import get_truth_snapshot
from tapjob_api.identity.models import User
from tapjob_api.jobs.models import Job, JobStatus

logger = logging.getLogger(__name__)

TAILORING_POLICY_VERSION = "2026-10-10.v2"


class TailoringError(Exception):
    """Raised when tailoring fails."""


@dataclass(frozen=True, slots=True)
class TailoredClaim:
    """Represents a discrete candidate claim grounded in specific TruthSnapshot facts."""

    claim_type: str
    text: str
    source_fact_ids: list[uuid.UUID]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "text": self.text,
            "source_fact_ids": [str(fid) for fid in self.source_fact_ids],
        }


@dataclass(frozen=True, slots=True)
class TailoredResumeDraft:
    job_id: uuid.UUID
    grounded_fact_ids: list[uuid.UUID]
    target_title: str
    sections: dict[str, str]
    claims: list[TailoredClaim]
    policy_version: str
    validation_result: dict[str, Any]
    ready_for_submission: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["job_id"] = str(self.job_id)
        data["grounded_fact_ids"] = [str(fid) for fid in self.grounded_fact_ids]
        data["claims"] = [c.to_dict() for c in self.claims]
        return data


def validate_tailored_claims(
    claims: list[TailoredClaim],
    snapshot_facts: dict[uuid.UUID, Any],
) -> tuple[bool, dict[str, Any]]:
    """Validates candidate claims at the claim level against canonical TruthSnapshot facts.

    Invariants:
    1. Zero candidate claims without source_fact_ids.
    2. All source_fact_ids must exist in TruthSnapshot.
    3. Claim text must be formed exclusively from fields of those facts.
    4. Zero fabricated qualification text (e.g. 'Profissional qualificado para...').
    """
    if not claims:
        return False, {
            "grounded": False,
            "claim_count": 0,
            "unverified_claims": ["zero_candidate_claims_generated"],
        }

    errors: list[str] = []
    for idx, claim in enumerate(claims):
        # 1. Zero candidate claim sem source_fact_ids
        if not claim.source_fact_ids:
            errors.append(f"claim_{idx}_{claim.claim_type}: missing_source_fact_ids")
            continue

        # 2. All source_fact_ids exist in TruthSnapshot
        missing_ids = [fid for fid in claim.source_fact_ids if fid not in snapshot_facts]
        if missing_ids:
            missing_str = [str(m) for m in missing_ids]
            errors.append(f"claim_{idx}_{claim.claim_type}: ungrounded_fact_ids:{missing_str}")
            continue

        # 3. Check for fabricated qualification regression
        lower_text = claim.text.lower()
        if "profissional qualificado para a vaga de" in lower_text:
            errors.append(f"claim_{idx}_{claim.claim_type}: fabricated_qualification_detected")
            continue

        # 4. Text must be derived from fields of source facts
        source_facts = [snapshot_facts[fid] for fid in claim.source_fact_ids]
        extracted_values: set[str] = set()
        for sf in source_facts:
            v = sf.value_json if isinstance(sf.value_json, dict) else {}
            for val in v.values():
                if isinstance(val, str) and val.strip():
                    extracted_values.add(val.strip().lower())
                elif isinstance(val, dict):
                    for subval in val.values():
                        if isinstance(subval, str) and subval.strip():
                            extracted_values.add(subval.strip().lower())

        has_grounding = any(ev in lower_text for ev in extracted_values)
        if not has_grounding:
            errors.append(f"claim_{idx}_{claim.claim_type}: claim_text_not_derived_from_facts")

    is_grounded = len(errors) == 0
    validation_result = {
        "grounded": is_grounded,
        "claim_count": len(claims),
        "unverified_claims": errors,
    }
    return is_grounded, validation_result


def generate_grounded_tailored_resume(
    session: Session,
    *,
    user: User,
    job_id: uuid.UUID,
) -> TailoredResumeDraft:
    """Generates a tailored resume draft strictly grounded in user's Truth Store."""
    snapshot = get_truth_snapshot(session, user=user)
    if not snapshot.facts:
        raise TailoringError(f"User {user.id} has no active profile facts to ground resume draft")

    job = session.execute(
        select(Job).where(
            Job.id == job_id,
            Job.status.in_([JobStatus.ACTIVE, JobStatus.UNKNOWN]),
        )
    ).scalar_one_or_none()
    if job is None:
        raise TailoringError(f"Job {job_id} not found or inactive")

    valid_facts_by_id = {f.id: f for f in snapshot.facts}

    claims: list[TailoredClaim] = []
    skill_claims: list[TailoredClaim] = []
    exp_claims: list[TailoredClaim] = []
    edu_claims: list[TailoredClaim] = []

    for fact in snapshot.facts:
        v = fact.value_json
        if not isinstance(v, dict):
            continue
        kind = v.get("kind")
        if kind == "skill":
            name = v.get("name")
            if name and str(name).strip():
                claim = TailoredClaim(
                    claim_type="skill",
                    text=str(name).strip(),
                    source_fact_ids=[fact.id],
                )
                skill_claims.append(claim)
                claims.append(claim)
        elif kind == "experience":
            employer = str(v.get("employer") or "").strip()
            title = str(v.get("title") or "").strip()
            if title or employer:
                text = f"{title} na {employer}" if (title and employer) else (title or employer)
                claim = TailoredClaim(
                    claim_type="experience",
                    text=text,
                    source_fact_ids=[fact.id],
                )
                exp_claims.append(claim)
                claims.append(claim)
        elif kind == "education":
            inst = str(v.get("institution") or "").strip()
            deg = str(v.get("degree") or "").strip()
            if deg or inst:
                text = f"{deg} - {inst}" if (deg and inst) else (deg or inst)
                claim = TailoredClaim(
                    claim_type="education",
                    text=text,
                    source_fact_ids=[fact.id],
                )
                edu_claims.append(claim)
                claims.append(claim)

    # Grounded summary claim: strictly composed from grounded claims
    summary_parts: list[str] = []
    summary_source_fact_ids: list[uuid.UUID] = []

    if exp_claims:
        summary_parts.append(f"Atuação em {'; '.join(c.text for c in exp_claims[:2])}")
        summary_source_fact_ids.extend([fid for c in exp_claims[:2] for fid in c.source_fact_ids])
    if skill_claims:
        summary_parts.append(f"Competências em {', '.join(c.text for c in skill_claims[:5])}")
        summary_source_fact_ids.extend([fid for c in skill_claims[:5] for fid in c.source_fact_ids])
    if not summary_parts and edu_claims:
        summary_parts.append(f"Formação em {'; '.join(c.text for c in edu_claims[:2])}")
        summary_source_fact_ids.extend([fid for c in edu_claims[:2] for fid in c.source_fact_ids])

    if summary_parts:
        summary_text = ". ".join(summary_parts) + "."
        summary_claim = TailoredClaim(
            claim_type="summary",
            text=summary_text,
            source_fact_ids=summary_source_fact_ids,
        )
        claims.append(summary_claim)
    else:
        summary_text = "Perfil profissional baseado exclusivamente nas informações confirmadas."

    # Neutral editorial text used for sections where no candidate facts exist
    sections: dict[str, str] = {
        "summary": summary_text,
        "skills": ", ".join(c.text for c in skill_claims)
        if skill_claims
        else "Nenhuma competência registrada",
        "experience_highlights": "; ".join(c.text for c in exp_claims)
        if exp_claims
        else "Nenhuma experiência registrada",
        "education": "; ".join(c.text for c in edu_claims)
        if edu_claims
        else "Nenhuma formação registrada",
    }
    if snapshot.headline:
        sections["headline"] = snapshot.headline

    is_grounded, validation_result = validate_tailored_claims(claims, valid_facts_by_id)

    grounded_fact_ids = list(valid_facts_by_id.keys())
    draft = TailoredResumeDraft(
        job_id=job.id,
        grounded_fact_ids=grounded_fact_ids,
        target_title=job.title,
        sections=sections,
        claims=claims,
        policy_version=TAILORING_POLICY_VERSION,
        validation_result=validation_result,
        ready_for_submission=is_grounded,
    )

    logger.info(
        "tailoring.draft_generated",
        extra={
            "user_id": str(user.id),
            "job_id": str(job.id),
            "grounded": is_grounded,
            "claim_count": len(claims),
            "fact_count": len(grounded_fact_ids),
        },
    )
    return draft
