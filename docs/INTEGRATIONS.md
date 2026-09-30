# Integrations & External Platforms — TapJob

**Research baseline:** 2026-09-29.  
Platform policies and APIs can change. Revalidate before implementation or launch.

## 1. Integration strategy

Every platform is classified by what we can safely support:

| Level | Meaning |
|---|---|
| A | Public/official discovery API |
| B | Official authenticated/partner API |
| C | User-assisted browser flow |
| D | Deep link only |
| X | Disabled pending legal/technical approval |

## 2. Gupy — priority Brazil

### Verified capabilities
Gupy exposes public documentation for a Quick Apply endpoint:

`POST /api/v2/applications/quick-apply`

Payload supports jobId, candidate identity/contact, LinkedIn URL, answers to question forms and curriculum in base64. The endpoint returns 201 on success.

However, Gupy API access requires a Bearer token generated within a customer account; official docs state API access is available to Premium/Enterprise customers and token generation requires appropriate account permissions.

### Product position
- Priority strategic partnership/integration.
- Build `GupyConnector` abstraction from day one.
- Do not assume TapJob can submit to every public Gupy job without a partnership/customer token.
- Until official route is available, use assisted/deep-link flow.

### Official references
- https://developers.gupy.io/v2.0/reference/applicationscontroller_createquickapplyapplication
- https://developers.gupy.io/v2.0/docs/autentica%C3%A7%C3%A3o-e-autoriza%C3%A7%C3%A3o
- https://developers.gupy.io/

## 3. LinkedIn

### Verified
LinkedIn Apply Connect exists for ATS/partner integrations. Access is restricted to approved developers/partners and requires agreements/certification. LinkedIn's legacy Job Posting API is not accepting new partnerships and directs applicants to Apply Connect.

### V1 strategy
- treat LinkedIn as external platform;
- save/open job links;
- allow user to paste a job URL/description;
- prepare application kit;
- extension-assisted form filling only after policy/legal validation;
- user remains responsible for final submission unless official access is granted.

### Do not
- make headless LinkedIn Easy Apply a production dependency;
- bypass CAPTCHA/rate limits;
- store user passwords.

### References
- https://learn.microsoft.com/en-us/linkedin/talent/apply-connect/apply-connect-overview
- https://learn.microsoft.com/en-us/linkedin/talent/apply-connect/create-configure-customer-application
- https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview

## 4. Indeed

Indeed offers partner-oriented Job Sync and Indeed Apply capabilities. Official docs describe ATS/approved partner integrations and a developer agreement/review process.

### V1
- discover via allowed/public channels where contractually permitted;
- save external links;
- application kit;
- assisted/deep-link apply.

### V2/Partnership
Pursue official partner integration before automated submission.

References:
- https://docs.indeed.com/indeed-apply
- https://docs.indeed.com/job-sync-api
- https://docs.indeed.com/job-sync-api/integrate-with-job-sync-api

## 5. Greenhouse

### Discovery
Greenhouse Job Board GET endpoints are public and require no authentication.

### Submission
The Job Board application POST endpoint requires Basic Auth using the employer's Job Board API key. This means TapJob cannot assume universal submission rights.

### Strategy
- discovery: V1;
- employer-authorized official application: later connector;
- otherwise deep-link/extension assist.

References:
- https://docs.greenhouse.io/job-board.html
- https://support.greenhouse.io/hc/en-us/articles/10568627186203-Greenhouse-API-overview

## 6. Lever

Lever's Postings API exposes published jobs and an application endpoint. POST application requires an API key generated for the Lever account and is rate-limited.

### Strategy
- public discovery: V1;
- official submission only with authorized employer key;
- otherwise hosted application link.

Reference:
- https://github.com/lever/postings-api

## 7. Ashby

Ashby exposes application form APIs; `applicationForm.submit` requires `candidatesWrite` permission and authenticated API access.

### Strategy
- public board discovery where available;
- official submit only with authorized API capability;
- otherwise assisted/deep link.

References:
- https://developers.ashbyhq.com/reference/applicationformsubmit
- https://developers.ashbyhq.com/docs/creating-a-custom-careers-page

## 8. Workday / SmartRecruiters / others

Treat as research/connectors, not assumptions.

For each platform before implementation:
1. confirm API;
2. confirm public vs customer credentials;
3. confirm application submission rights;
4. confirm rate limits;
5. confirm ToS/automation restrictions;
6. define fallback mode.

## 9. Gmail

### Goal
Detect lifecycle events:
- application received;
- assessment;
- interview;
- recruiter contact;
- rejection;
- offer;
- deadlines.

### Official constraints
`gmail.readonly` is a Restricted Scope. Public applications using restricted scopes require OAuth verification; storing/transmitting restricted-scope data server-side can require a security assessment.

Gmail push uses Cloud Pub/Sub and `users.watch`; watch must be renewed at least every 7 days, with daily renewal recommended.

### Architecture
- explicit opt-in;
- minimal query first;
- metadata/snippet prefilter;
- fetch full body only when necessary;
- no write/delete permission in V1;
- store normalized event, avoid storing full body unless required;
- disconnect/delete controls.

References:
- https://developers.google.com/workspace/gmail/api/auth/scopes
- https://developers.google.com/workspace/gmail/api/guides/push

## 10. Outlook

V2 target:
- Microsoft Graph OAuth;
- mailbox event interpretation;
- calendar linkage.

No implementation assumptions before dedicated research.

## 11. WhatsApp

### Target
Official WhatsApp Business Platform adapter for production.

Capabilities:
- inbound commands;
- interactive approvals;
- proactive notifications under allowed messaging rules;
- deep links to app;
- document delivery where appropriate.

### Adapter design
`MessagingChannel`:
- receive;
- send;
- sendTemplate;
- sendDocument;
- sendInteractive;
- delivery status.

A self-hosted adapter such as Evolution API may be used for controlled development experiments, but production strategy must remain compatible with official platform requirements.

## 12. Calendar

V2:
- Google Calendar
- Outlook Calendar

V1 can maintain internal events/reminders without external calendar write access.

## 13. Job source backlog for Brazil

Research required:
- Sólides;
- Vagas.com.br;
- Catho;
- InfoJobs;
- Empregos.com.br;
- Trabalha Brasil;
- company career pages;
- public-sector sources when applicable.

Each source receives a connector dossier before code.

## 14. Integration dossier template

Every new connector must document:

- owner/platform;
- official docs;
- auth model;
- discovery access;
- application access;
- status sync;
- rate limits;
- legal/ToS notes;
- personal data;
- secret storage;
- fallback mode;
- tests;
- kill switch;
- monitoring;
- last verification date.