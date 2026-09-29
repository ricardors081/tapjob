# Product Surfaces — Mobile, WhatsApp, Web & Extension

## 1. One product, multiple surfaces

All channels operate over the same Career Core.

No channel owns business state.

## 2. Mobile app

### Proposed navigation

Bottom tabs:
1. Home
2. Vagas
3. Aplicações
4. Agente
5. Perfil

Secondary routes:
- Currículos
- Application Kits
- Entrevistas
- Contatos
- Tarefas
- Documentos
- Analytics
- Integrações
- Configurações

### Home

Cards:
- vagas novas de alto match;
- candidatura aguardando ação;
- entrevista próxima;
- follow-up vencendo;
- respostas detectadas;
- resumo de progresso semanal;
- recomendação principal do agente.

### Vagas

Features:
- personalized feed;
- search;
- filters;
- score;
- freshness;
- source;
- save/dismiss;
- "por que combina?";
- "preparar candidatura";
- watchlists.

### Job Detail

Show:
- company/title/location/workplace;
- source and original link;
- salary if available;
- match score;
- blockers;
- matched skills;
- missing skills;
- description;
- company notes;
- matching resume;
- application mode;
- actions.

### Applications

Views:
- Kanban;
- list;
- calendar/timeline.

Status:
Discovered, Saved, Preparing, Ready, Applied, Assessment, Interview, Offer, Hired, Rejected, Withdrawn, Expired.

### Resume Studio

- master resume;
- versions;
- templates;
- ATS analysis;
- keyword comparison;
- tailor for job;
- preview;
- PDF;
- later DOCX.

### Agent

Persistent chat with contextual shortcuts:
- "Ache vagas para mim"
- "O que devo priorizar hoje?"
- "Melhore meu currículo"
- "Prepare essa vaga"
- "Me prepare para entrevista"
- "Como estão minhas candidaturas?"

### Profile

- truth store;
- goals;
- preferences;
- answers;
- linked accounts;
- privacy;
- notification preferences.

## 3. WhatsApp

WhatsApp is the fastest interface, not the database.

### Inbound examples
- "mais vagas remotas"
- "prepare essa"
- "quais entrevistas tenho?"
- "melhore meu currículo"
- "essa vaga vale a pena?"
- "marque como rejeitada"
- "me lembra amanhã"

### Outbound examples
- high-match job;
- recruiter response detected;
- interview reminder;
- follow-up due;
- application kit ready;
- weekly summary.

### Approval flow

Example:
1. Agent: "Encontrei vaga 91% compatível. Preparar?"
2. User: "Sim"
3. Agent creates kit.
4. Agent: "Currículo e respostas prontos. Revisar no app ou abrir candidatura?"
5. Deep link to app/application.

### Channel rules
- opt-in required;
- quiet hours;
- throttle;
- template messaging when required by platform policy;
- user can disable proactive messages independently from account.

## 4. Web

Not required for first mobile beta if bandwidth is constrained.

When introduced:
- rich resume editor;
- analytics;
- application management;
- settings;
- admin/ops;
- integration setup.

## 5. Browser Extension

Purpose: reduce application friction on desktop.

### V1.x
- detect supported page;
- identify form labels/types;
- map safe fields;
- preview fill plan;
- fill profile;
- attach chosen resume;
- surface generated answers;
- mark unresolved questions;
- sync application result back to Tap Vaga after user confirmation.

### Non-goals
- silently apply to unlimited jobs;
- bypass anti-bot/CAPTCHA;
- capture passwords;
- exfiltrate unrelated page content.

## 6. Deep linking

Canonical links:
- tapvaga://jobs/{id}
- tapvaga://applications/{id}
- tapvaga://resumes/{id}
- tapvaga://interviews/{id}
- tapvaga://agent?context=...

All external notifications should deep-link to the relevant entity.

## 7. Offline behavior

Mobile can cache:
- recent jobs;
- application list;
- profile snapshot;
- resume metadata.

Writes queue only for explicitly safe actions. Submission actions require online confirmation.

## 8. Accessibility

- dynamic text;
- screen reader labels;
- sufficient contrast;
- tap targets;
- avoid color-only status;
- semantic loading/error states.

## 9. Reference reuse

### Basafy
- mobile-first pipeline;
- Gmail deep link;
- push;
- application timeline.

### Resumint
- resume flows;
- ATS screens;
- AI suggestions;
- export.

### CareerTrack
- notifications;
- dashboard;
- camera/document interactions.

### HireHub
- realtime chat architecture;
- applications;
- job feed.

### CareerOS
- job/career shell and mobile product map.
