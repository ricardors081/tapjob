# Reference Products & Source Projects — Tap Vaga

## 1. Commercial product references

These are product/UX references only.

### AIApply
Reference opportunity:
- application execution;
- tailored documents;
- application automation concepts;
- interview tools.

### Huntr
Reference opportunity:
- career workspace;
- application tracking;
- resume management;
- contacts;
- analytics.

### Simplify
Reference opportunity:
- job discovery;
- autofill/application friction reduction;
- extension-first workflows;
- tracking.

Tap Vaga must not copy branding or proprietary implementation.

## 2. Assistant/backend source references

### Gsync/jobsync
https://github.com/Gsync/jobsync

Use for:
- application tracker;
- resume import/export;
- AI assistant tool calling;
- contacts/companies/interviews/tasks;
- Greenhouse/Lever/Ashby discovery;
- MCP patterns;
- automated matching;
- tests/evals.

Verified license: MIT.

### roysahar11/appliable
https://github.com/roysahar11/appliable

Use for:
- personal career-agent model;
- guided profile setup;
- multi-source job pipeline;
- resume tailoring;
- WhatsApp delivery concepts;
- batch orchestration.

Verified license: MIT.

### AbhishekMandapmalvi/AutoApply
https://github.com/AbhishekMandapmalvi/AutoApply

Use for:
- application pipeline;
- knowledge base;
- resume assembly;
- per-ATS adapter architecture;
- Playwright form handling;
- review mode;
- scoring/lifecycle.

Verified license: MIT.

Important:
browser automation is research/reference; production use must follow Tap Vaga Apply Engine policy.

### career-ops-hq/career-ops
https://github.com/career-ops-hq/career-ops

Use for:
- career reasoning;
- ATS CV generation;
- job evaluation;
- interview prep;
- story bank;
- rejection-pattern analytics;
- negotiation;
- company/job discovery.

Verified license: MIT.

### wardoep/jobbot
https://github.com/wardoep/jobbot

Use for:
- ingestion -> normalize -> dedupe -> filter -> score -> alert architecture;
- inbox watcher;
- Telegram;
- multi-channel notification separation;
- application kits;
- FastAPI patterns.

Repository currently has no LICENSE file observed during audit.
Project owner reports author permission was granted. Archive evidence before code import.

## 3. Mobile source references

### codeharsh27/Career-OS
https://github.com/codeharsh27/Career-OS

Use for:
- mobile information architecture;
- application flow;
- career guidance concepts;
- resume/job feature organization.

Audit warning:
hardcoded API credentials were observed in source. Never copy secrets. Rotate/replace if any code is imported.

No LICENSE file observed during audit.
Owner permission reported; archive evidence.

### Talia04/Basafy
https://github.com/Talia04/Basafy

High-value reference for:
- Gmail OAuth;
- staged inbox retrieval;
- heuristic + LLM classification;
- application/event linking;
- dedupe;
- push;
- Supabase Edge Function patterns;
- timeline/insights.

No LICENSE file observed during audit.
Owner permission reported; archive evidence.

### prataps9/Resumint
https://github.com/prataps9/Resumint

Use for:
- Expo resume UX;
- builder/editor/preview;
- ATS score UI;
- job tailoring;
- cover letter;
- PDF/export;
- AI suggestion components.

Audit warning:
example code calls AI providers from mobile using public Expo environment values. Tap Vaga must move provider secrets/calls to backend.

No LICENSE file observed during audit.
Owner permission reported; archive evidence.

### KaveenDK/CareerTrack-Mobile-App
https://github.com/KaveenDK/CareerTrack-Mobile-App

Use for:
- Expo tracker;
- push/local reminders;
- dashboard;
- camera/document capture;
- follow-up composer.

README states MIT, but no root LICENSE file was observed during audit.
Owner permission reported; archive evidence.

### akashpd390/HireHub
https://github.com/akashpd390/HireHub

Use for:
- Supabase realtime chat;
- job feed;
- bookmarks;
- application status;
- resume upload.

Verified license: MIT.

## 4. Reuse protocol

Before importing code from any source:

1. pin source repository and commit SHA;
2. identify exact files/functions;
3. confirm permission/license evidence;
4. record modifications;
5. remove secrets and source-specific configuration;
6. run security review;
7. add tests;
8. attribute when required;
9. document imported module in PR.

## 5. Permission note

The Tap Vaga project owner reports that all referenced repository owners were contacted and authorized reuse for this project.

This documentation records that report but is not itself legal evidence. Screenshots/e-mails/messages or other proof of approval should be archived privately and, when appropriate, summarized in repository documentation without exposing personal data.

## 6. Do not create a Frankenstein

Reuse is selective.

Preferred order:
1. adopt domain insight;
2. reuse isolated tested component;
3. adapt behind Tap Vaga interface;
4. add canonical tests;
5. discard source-specific coupling.

Tap Vaga's architecture remains authoritative.
