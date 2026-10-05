# Source Reuse Ledger — TapJob

This ledger becomes mandatory when any upstream code, prompt, fixture, test, SQL or asset is incorporated into TapJob.

## Permission evidence

The project owner states that all 10 upstream owners authorized reuse and that confirmation evidence is retained in e-mail.

Private authorization correspondence must not be committed here. For repositories without a LICENSE file, a reuse PR should state `owner authorization — evidence retained privately` and the date the project owner confirms the authorization.

## Ledger

| TapJob PR | Upstream | Upstream SHA | Source path | Destination | Mode | Permission basis | Tests/evals ported | Security notes |
|---|---|---|---|---|---|---|---|---|
| WP1.2 (branch `feat/wp1-2-career-profile-truth-store`) | `career-ops-hq/career-ops` | `d6a6c12db6d579ae0f3e6875d247409f6b8c9806` | `career-profile.mjs` (fact identity/evidence/review_status model, lines ~1-352); quoted invariant at line 4, `"Parsing is deliberately conservative: the CV is evidence, not an instruction."` | `services/api/src/tapjob_api/career_profile/models.py` (`ProfileFact.source_ref`/`evidence_ref`/`user_confirmed`/`confirmed_at`) | PORT | owner authorization — evidence retained privately | No upstream tests ported (donor is a Node CLI script with no test suite at this path); TapJob wrote its own fact-provenance/immutability test suite instead | Donor MUTATES a fact's `text` in place on re-import (no supersession/history) — this exact behavior was explicitly REJECTED, not ported: TapJob instead makes every `ProfileFact` immutable after creation and requires a new row + `supersedes_fact_id` link for any content change (see ADR-0010 §"Fact immutability"). No secrets, filesystem-as-database, or Node runtime assumptions were carried over; donor's `fact.id` content-hash scheme was also not ported (TapJob uses a plain server-generated UUID) |
| WP1.2 (branch `feat/wp1-2-career-profile-truth-store`) | `roysahar11/appliable` | `2a900e892420039d228ceeb63f03b0480eb72fe4` | `.claude/skills/customize-resume/SKILL.md` ("Source of Truth Discipline" section, ~lines 1-60) | `services/api/src/tapjob_api/career_profile/service.py` (`get_truth_snapshot` default filter: confirmed + active + not retracted/superseded only) | ADAPT | owner authorization — evidence retained privately | No upstream tests ported (donor is a prompt/skill markdown file, not executable code); TapJob wrote its own truth-snapshot filter test suite instead | Donor's binary "approved source or not usable at all" gate (no confidence-weighted inclusion) is the discipline ADAPTED into TapJob's `TruthSnapshot`, which never exposes an unconfirmed fact. Donor's filesystem-folder-as-source-of-truth architecture (`CLAUDE.md`: "The directory structure is the source of truth") was explicitly NOT imported — TapJob's canonical PostgreSQL model remains the only source of truth |

## Mode values

- IMPORT
- ADAPT
- PORT

REFERENCE-only usage does not require a ledger row unless source assets/text are copied.

## Import checklist

- [ ] upstream SHA pinned;
- [ ] source paths listed;
- [ ] permission/license basis recorded;
- [ ] secrets removed;
- [ ] source-specific endpoints/config removed or abstracted;
- [ ] canonical TapJob interface used;
- [ ] tests ported/created;
- [ ] attribution/notices preserved where required;
- [ ] security review complete;
- [ ] no duplicate competing implementation introduced.