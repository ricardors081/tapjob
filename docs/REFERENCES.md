# Reference Products & Source Projects — TapJob

**Latest source audit:** 2026-09-30

This file records the external products and repositories used as references. The exact reuse decision by capability is authoritative in [SOURCE_REUSE_BLUEPRINT.md](SOURCE_REUSE_BLUEPRINT.md). Any code-bearing reuse must be recorded in [SOURCE_REUSE_LEDGER.md](SOURCE_REUSE_LEDGER.md).

## 1. Commercial product references

Product/UX references only:

- **AIApply:** application execution, tailored documents, automation concepts and interview tools.
- **Huntr:** career workspace, application tracking, resume management, contacts and analytics.
- **Simplify:** job discovery, autofill/application-friction reduction, extension-first workflows and tracking.

TapJob must not copy proprietary implementation or branding from commercial products.

## 2. Audited source repositories

| Repository | Audited SHA | License observed | Canonical TapJob use |
|---|---|---|---|
| `Gsync/jobsync` | `ef3d7eec5c9aebaa1c6e03fc3ddd62d7dca8e37f` | MIT | ATS discovery edge cases/tests, agent tools, prompts/evals, domain behavior |
| `roysahar11/appliable` | `2a900e892420039d228ceeb63f03b0480eb72fe4` | MIT | Claude workflows, resume truth discipline, WhatsApp/job orchestration UX |
| `AbhishekMandapmalvi/AutoApply` | `053071ba1bba5733b522d78c3d645002d817e55a` | MIT | Python resume modules and assisted-apply field/selector logic |
| `career-ops-hq/career-ops` | `d6a6c12db6d579ae0f3e6875d247409f6b8c9806` | MIT | career intelligence, source-backed profile, application/interview playbooks and evals |
| `wardoep/jobbot` | `5932b3782412aeda4ed492df7c788b1248121ecb` | no root LICENSE observed | primary Python donor for job ingestion, filters, matching and alerts |
| `codeharsh27/Career-OS` | `28efc4d9d71f390d746241f9d6e85153b88078a3` | no root LICENSE observed | product/mobile flow reference only; Flutter code is not part of TapJob runtime |
| `Talia04/Basafy` | `e11c0e69a968d1325e0ab3662c0e6fce875c7ea9` | no root LICENSE observed | primary Gmail intelligence reference; Expo application/inbox/push UI donor |
| `prataps9/Resumint` | `cfc581a7e429122e362ffdaf0162978b3ed1865c` | no root LICENSE observed | primary Expo Resume Studio UI donor |
| `KaveenDK/CareerTrack-Mobile-App` | `2ea6cdc28b8c2dc73febbf5588d70f396759bb4e` | no root LICENSE observed | Expo tracker, reminders, camera/document components |
| `akashpd390/HireHub` | `17d9d7283f406e6cfb3ca18a32649fb57dd2edc0` | MIT | recruiter/realtime/bookmark UX reference; Flutter code is not imported directly |

Repositories:
- https://github.com/Gsync/jobsync
- https://github.com/roysahar11/appliable
- https://github.com/AbhishekMandapmalvi/AutoApply
- https://github.com/career-ops-hq/career-ops
- https://github.com/wardoep/jobbot
- https://github.com/codeharsh27/Career-OS
- https://github.com/Talia04/Basafy
- https://github.com/prataps9/Resumint
- https://github.com/KaveenDK/CareerTrack-Mobile-App
- https://github.com/akashpd390/HireHub

## 3. Permission basis

The TapJob project owner reports that all 10 repository owners were contacted and that authorization for reuse was formalized and retained by e-mail.

The private correspondence is not committed to this repository. For repositories without a visible LICENSE file, reuse PRs must record the basis as:

`owner authorization — evidence retained privately`

and must still pin the upstream SHA/path in the Source Reuse Ledger.

## 4. Important audit findings

### JobSync
Strong source for tests/evals, ATS connector edge cases, resume-import prompts, agent tool architecture, contacts/interviews/tasks and dedupe. Do not import the Next.js/Prisma/SQLite application wholesale.

### Appliable
Strong source for Claude Code skills/agents, two-pass job evaluation, tailored-resume source-of-truth discipline and WhatsApp reporting. Browser automation/WAHA and filesystem state are not production architecture.

### AutoApply
Strongest direct Python donor for resume parser/scorer/assembler/renderer and ATS form mechanics. Final-submit automation, CAPTCHA behavior and sensitive question handling must be replaced by TapJob policy.

### career-ops
Strongest career-intelligence donor. Preserve source-backed facts, application-answer discipline, interview plan/practice/debrief, story banks, prompt-injection treatment and eval fixtures. Port data persistence to PostgreSQL.

### JobBot
Primary Python donor for the job-source contract, Greenhouse/Lever/Ashby adapters, normalization, hard filters, scorer pipeline and alert isolation. Do not adopt its ORM/auth/IMAP-password model wholesale.

### CareerOS
Useful for Discover → Match → Track → Prepare UX, cold-start matching and skill-gap concepts. Flutter is incompatible with the canonical Expo stack. Hardcoded job API credentials were observed; never reuse them.

### Basafy
Highest-value Gmail donor: query buckets, staged fetch, parsers, entity normalization, application resolution/adjudication, dedupe, observability, push and mobile sync UX. Port backend logic from Supabase/Deno to Python workers/PostgreSQL.

### Resumint
Primary Resume Studio mobile donor: editor, score presentation, job tailoring, preview/templates and Zustand draft-state patterns. Direct client-side AI calls/keys must not be carried into TapJob.

### CareerTrack
Useful Expo donor for tracking screens, search/filter UI, camera/document capture and interview reminders. Replace Firebase/data services with TapJob API and canonical states/tokens.

### HireHub
Useful future UX reference for realtime recruiter chat, bookmarks and resume selection. Direct Flutter import is excluded.

## 5. Reuse protocol

Before importing code, prompt assets, SQL, tests, fixtures or other source artifacts:

1. consult `SOURCE_REUSE_BLUEPRINT.md`;
2. pin source repository + audited/re-audited commit SHA;
3. identify exact upstream paths/functions;
4. choose IMPORT / ADAPT / PORT;
5. record permission/license basis;
6. scan for secrets and unsafe source assumptions;
7. fit the code behind TapJob canonical interfaces;
8. port/create tests and evals;
9. add a row to `SOURCE_REUSE_LEDGER.md`;
10. preserve attribution/notices where applicable.

A copied artifact without provenance is a merge blocker.

## 6. Anti-Frankenstein rule

TapJob has one canonical:
- data model;
- application state machine;
- source/connector contract per capability;
- AI Gateway;
- Apply Engine;
- notification abstraction;
- mobile stack.

When two upstream projects solve the same problem, the blueprint selects a primary donor. Secondary repositories contribute tests, edge cases or ideas rather than a second competing runtime implementation.