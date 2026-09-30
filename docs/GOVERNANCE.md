# Engineering & Product Governance — TapJob

## 1. Goal

Keep GitHub as durable source of truth so any future human or AI agent can continue the project safely.

## 2. Branch policy

Canonical target:
- `main`

Desired technical enforcement:
- protect `main`;
- require PR;
- require latest-HEAD status checks;
- require conversation resolution.

If the active GitHub plan for a private repository does not support these protections, the same requirements remain mandatory as a procedural governance gate until the plan/capability changes. Do not claim GitHub is enforcing a rule that the repository settings cannot actually enforce.

Suggested branches:
- `docs/<topic>`
- `feat/<topic>`
- `fix/<topic>`
- `refactor/<topic>`
- `security/<topic>`
- `spike/<topic>`

No direct feature commits to main.

## 3. Pull request lifecycle

1. define scope;
2. create branch;
3. implement/document;
4. self-check;
5. run tests/lint/typecheck;
6. open PR;
7. independent review;
8. address review findings;
9. verify CI at latest HEAD;
10. merge.

A PR must not be approved based only on an agent summary.

### Pre-CI bootstrap exception

Before WP0.1 installs the first CI workflow, GitHub has no checks to turn green. During this narrow bootstrap window, a PR may merge without CI only when all conditions below are true:

- repository has no CI/check workflow configured;
- PR is documentation/foundation only and contains no runtime feature implementation;
- the real GitHub diff was independently reviewed;
- the latest HEAD SHA was verified immediately before merge;
- there are no unresolved review threads;
- GitHub reports the PR as mergeable;
- the merge record states that the pre-CI exception was used.

This exception expires automatically when WP0.1 adds CI. From that point forward, latest-HEAD green CI is mandatory. GitHub branch protection should enforce it when available; otherwise the reviewer/merge operator must verify it manually before merge.

## 4. PR body template

Every material PR should include:

### Objective
What problem is being solved.

### Scope
Included / excluded.

### Changes
Files/modules.

### Tests
Commands and results.

### Security/Privacy
Impact and controls.

### External impact
APIs, migrations, env vars, infra.

### Evidence
CI, screenshots, logs where relevant.

### Docs
Which canonical docs were updated.

### Rollback
How to revert safely.

## 5. Review rules

Reviewer verifies:
- diff;
- tests;
- docs;
- security;
- secrets;
- schema/migrations;
- backward compatibility;
- external API assumptions;
- observability;
- failure paths.

## 6. Decision governance

Use `docs/DECISIONS.md` for current decision register.

Create dedicated ADR under `docs/adr/` when decision:
- changes architecture;
- adds major vendor;
- affects security/privacy;
- changes data model semantics;
- changes application policy;
- introduces irreversible coupling.

ADR states:
- Proposed
- Accepted
- Superseded
- Rejected
- Deprecated

## 7. Documentation authority

Priority when conflict exists:
1. accepted ADR;
2. PRD/business rules;
3. Architecture/Integration docs;
4. Project State;
5. implementation comments;
6. agent/chat summaries.

Conflicts must be fixed; do not silently choose.

## 8. Quality gates

Before merge, as applicable:
- unit tests;
- integration tests;
- typecheck;
- lint;
- formatting;
- migrations test;
- dependency scan;
- secret scan;
- AI evals;
- mobile build;
- E2E.

## 9. Schema changes

Every DB change:
- Alembic migration;
- forward path;
- downgrade/rollback stance;
- data migration safety;
- test on realistic data;
- no destructive migration without explicit approval.

## 10. External connectors

A connector PR must include:
- official docs;
- verified date;
- capability manifest;
- auth;
- rate limits;
- privacy;
- ToS/legal assumptions;
- fallbacks;
- tests;
- kill switch.

## 11. AI changes

Any prompt/model/tool policy change that can affect external artifacts must include:
- version;
- eval impact;
- grounding behavior;
- failure behavior;
- cost impact where material.

## 12. Security changes

Security findings are blocking when they involve:
- secret exposure;
- auth bypass;
- IDOR/BOLA;
- unrestricted external side effects;
- token leakage;
- sensitive data logging;
- unsafe file upload;
- prompt injection -> privileged tool action.

## 13. Repository conventions

Target structure:

```text
/
  AGENTS.md
  README.md
  docs/
  apps/
    mobile/
    web/          # when approved
    extension/    # when approved
  services/
    api/
    worker/
  packages/
    contracts/    # if shared types/schema adopted
  infra/
  .github/
```

Final scaffold can change by ADR.

## 14. Source reuse

`docs/SOURCE_REUSE_BLUEPRINT.md` defines the approved donor and reuse mode for each capability. `docs/SOURCE_REUSE_LEDGER.md` is the mandatory provenance record.

When importing or porting upstream code, prompts, tests, fixtures, SQL or assets:
- use the primary donor selected by the blueprint unless an ADR/decision changes it;
- pin source repository + full commit SHA + exact paths/functions;
- classify as IMPORT / ADAPT / PORT;
- record license/owner authorization basis;
- record destination and material modifications;
- remove secrets and source-specific unsafe assumptions;
- port/create tests and evals;
- preserve attribution/notices when applicable;
- add/update the Source Reuse Ledger in the same PR.

A code-bearing reuse PR without a complete ledger entry is blocked. No anonymous copy/paste and no duplicate competing runtime implementation for the same capability.

## 15. Definition labels

Allowed:
- PLANNED
- IN PROGRESS
- IMPLEMENTED
- VERIFIED
- BLOCKED
- DEFERRED

Avoid ambiguous "done" without verification.

## 16. Release rule

A release requires:
- tagged commit;
- migrations accounted for;
- release notes;
- rollback plan;
- environment validation;
- monitoring ready.