# Contributing to Tap Vaga

Read [AGENTS.md](AGENTS.md) and [docs/GOVERNANCE.md](docs/GOVERNANCE.md) before making changes.

## Standard flow

1. Read current state.
2. Confirm authorized work package.
3. Create scoped branch.
4. Implement with tests.
5. Update docs when behavior/architecture/state changes.
6. Open PR.
7. Resolve review findings.
8. Merge only with green CI and verified latest HEAD.

Before WP0.1 establishes CI, the only allowed exception is the documented pre-CI bootstrap exception in `docs/GOVERNANCE.md`. It does not apply to feature/runtime code.

## Commit guidance

Prefer conventional prefixes:
- feat:
- fix:
- docs:
- refactor:
- test:
- security:
- chore:

## Pull requests

Keep PRs reviewable. Do not combine unrelated architecture, features and cleanup.

For external integrations include official documentation links and last verification date.

For source reuse include original repository and commit SHA.

## Security

Never commit:
- API keys;
- OAuth tokens;
- passwords;
- private resumes/user data;
- production dumps.

Report security-sensitive findings privately rather than opening public exploit details.
