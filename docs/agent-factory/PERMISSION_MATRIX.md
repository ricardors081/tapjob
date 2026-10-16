# TapJob Agent Factory — Permission Matrix

This document defines the authority, execution boundaries, and permissions for all agent roles within the TapJob Antigravity 2.0 multi-agent orchestrator.

## 1. Core Operating Principles

1. **Least Privilege**: Each agent role possesses only the permissions necessary to fulfill its specialization.
2. **Separation of Duties**: The agent that implements code cannot approve or merge that code.
3. **No Direct Main Commits**: No agent may commit directly to `main` or `master`.
4. **No Autonomous Production Deployments**: Production modifications and real-world side effects are strictly gated by human authorization.
5. **Defense-in-Depth**: Role permissions are enforced through Antigravity PreToolUse guards, local git hooks, and CI quality gates.

---

## 2. Role Permission Matrix

| Role | Permitted Actions | Prohibited Actions | Human Authorization Required For |
| :--- | :---------------- | :----------------- | :-------------------------------- |
| **Engineering Manager** | • Decomposes tasks & plans work<br>• Invokes dynamic specialists<br>• Coordinates isolated Git worktrees<br>• Creates branches, commits, opens PRs | • Direct commits/pushes to `main`<br>• Self-approval or merge of PRs<br>• Production deployments<br>• Direct real messaging sends | • Task scope expansion<br>• Budget escalations<br>• Architecture tradeoffs |
| **Core Platform Engineer** | • Implements Career Core (`services/api`, `services/worker`)<br>• Writes unit, integration, and contract tests<br>• Runs local builds and validations | • Altering channel-specific code without contract coordination<br>• Production deployments<br>• PR merge authority | • Database schema breaking changes<br>• New external dependencies |
| **Channel / WhatsApp Engineer** | • Implements channel adapters and provider integrations<br>• Configures Evolution API client wrappers<br>• Writes provider mocking & test suites | • Implementing business rules outside Career Core<br>• Sending unapproved messages to third parties<br>• PR merge authority | • Staging Evolution configuration changes<br>• Real canary send authorization |
| **QA / SDET Engineer** | • Creates integration, contract, and adversarial test suites<br>• Executes synthetic scenarios and stress tests<br>• Validates regression boundaries | • Bypassing tests or relaxing assertions to fake success<br>• Modifying core product logic<br>• PR merge authority | • Execution of live canary suites against staging |
| **Reviewer / Security Engineer** | • Conducts independent diff, security, and architectural reviews<br>• Inspects secret handling, PII sanitation, and dependencies<br>• Operates predominantly in read-first mode | • Reviewing/approving own code<br>• PR merge authority during baseline governance<br>• Modifying product code during review | • Escalation of blocking security findings |
| **Release / Platform / Ops Engineer** | • Staging environment maintenance under explicit task scope<br>• Docker image configuration and deployment contracts<br>• Build optimization and CI workflow tuning | • Deploying to production autonomously<br>• Smuggling product logic changes into deployment PRs<br>• PR merge authority | • Production deployment<br>• Secret or credential rotation<br>• DNS or infrastructure modifications |
| **Conversation / Product UX Designer** | • User journey specifications and prompt copy design<br>• Interaction states and conversation guidelines<br>• Documentation of conversational UX flows | • Implementing undocumented business rules<br>• Committing untested backend logic<br>• PR merge authority | • Major conversational tone or scope shifts |
| **Future Mobile Designer** | • Information Architecture (IA) and screen journey specs<br>• Design system tokens coordination (`@tapjob/design-tokens`)<br>• Accessibility guidelines and interaction states | • Production code modifications<br>• Direct PR merge authority | • Design system breaking changes |
| **Future Mobile Engineer** | • React Native / Expo shell and screens implementation<br>• Consumes Career Core contracts (`@tapjob/contracts`)<br>• Mobile-specific unit and component tests | • Duplicating backend business logic in mobile client<br>• Committing hardcoded credentials or API URLs<br>• PR merge authority | • Native build configuration alterations |

---

## 3. Dynamic / On-Demand Specialists

Dynamic specialists may be instantiated by the Engineering Manager to tackle specialized tasks:

- **AI / Matching Specialist**: Tuning matching algorithms, ranking prompts, and ATS evaluation logic against ground-truth datasets.
- **Database Specialist**: Designing complex migrations, index optimization, and PostgreSQL performance profiling.
- **Deep Security Specialist**: Threat modeling, taint analysis, authentication hardening, and prompt injection mitigation.
- **Performance / Reliability Specialist**: Load testing, Redis queue tuning, and latency optimization.
- **Accessibility Specialist**: WCAG 2.1 compliance, screen-reader validation, and contrast auditing.
- **Product / Data Analyst**: Career analytics metrics, telemetry design, and conversion tracking specifications.

Dynamic specialists operate under the same invariants: work in isolated branches/worktrees, require independent QA and review, and have zero merge or production authority.

---

## 4. MCP & Custom Tool Authority Boundaries

1. **GitHub MCP Server Policy**: Tools offering automated pull request merge, pull request approval, or direct branch reference updates are disabled during baseline governance. No agent role may exercise PR merge authority through MCP tools.
2. **Evolution API & Live Transport MCP**: MCP tools exposing messaging endpoints are subject to Tier C human gates. Direct sends to real candidates or non-canary numbers are strictly prohibited.
3. **Parity with PreToolUse Guards**: Introduction of MCP servers must never circumvent client-side safety invariants. Any tool invoked via MCP must adhere to the same least-privilege boundaries as CLI terminal executions.

