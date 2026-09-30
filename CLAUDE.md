# CLAUDE.md — TapJob

This file is the Claude Code entrypoint. It is intentionally short.

## Canonical instructions

**Read and obey `AGENTS.md` first.** It contains the mandatory reading order, governance, security rules and Definition of Done.

Do not treat this file as a replacement for `AGENTS.md`.

## Before editing anything

1. Read `AGENTS.md` and its mandatory document sequence.
2. Read `docs/PROJECT_STATE.md`.
3. Confirm the currently authorized work package/checkpoint in `docs/IMPLEMENTATION_PLAN.md`.
4. If upstream code may be reused, read:
   - `docs/SOURCE_REUSE_BLUEPRINT.md`
   - `docs/SOURCE_REUSE_LEDGER.md`
5. Read `docs/ENGINEERING_STANDARDS.md` and `docs/DESIGN_SYSTEM.md` before creating runtime/config/UI foundations.
6. Follow `docs/CLAUDE_CODE_WORKFLOW.md`.

## Hard rules

- GitHub/`main` is the source of truth.
- Never implement outside the authorized work package.
- Do not silently change Accepted decisions.
- Do not introduce a second implementation where the reuse blueprint already selects a primary donor.
- Any imported/ported upstream code, prompt, test, fixture, SQL or asset requires a ledger entry with repo + full SHA + source path.
- Never copy upstream secrets, credentials or insecure secret-placement patterns.
- Runtime state must use TapJob canonical models/interfaces, not an upstream project's storage model.
- Do not hardcode product name, visual values, cross-cutting URLs/timeouts/status strings or other values owned by tokens/config/domain policy.
- Treat every `EXPO_PUBLIC_*` value as public; secrets stay server-side.
- Do not claim completion from a report. Verify files, tests, CI and the real PR HEAD.
- No merge before the gates in `docs/GOVERNANCE.md`.

## Recommended workflow

For non-trivial work:
1. use `grill-me` when requirements/design still contain ambiguity;
2. use the approved feature-development workflow;
3. implement one reviewable checkpoint;
4. run tests/lint/typecheck/security/provenance checks;
5. update docs/state;
6. open PR;
7. run code review;
8. resolve findings;
9. verify CI at latest HEAD;
10. merge only under repository governance.

When a skill/plugin conflicts with repository documentation, repository documentation wins.