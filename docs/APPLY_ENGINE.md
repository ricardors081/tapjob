# Apply Engine — Tap Vaga

## 1. Objective

Reduce friction in applications without building the product on brittle or unauthorized automation.

## 2. Canonical modes

### OFFICIAL_API
Use when Tap Vaga has documented right/capability to submit.

Flow:
1. fetch form/schema;
2. validate eligibility;
3. assemble application kit;
4. present review according to user policy;
5. submit with idempotency key;
6. persist provider application ID;
7. audit request/result;
8. retry only safe failures.

### ASSISTED_EXTENSION
Use when application happens in the user's browser.

Extension:
- identifies fields;
- sends schema/labels to backend;
- backend maps fields to confirmed profile facts;
- user sees proposed fills;
- extension fills;
- file upload selects approved resume;
- unknown/legal/sensitive questions require user input;
- user clicks Submit unless a later verified integration explicitly allows otherwise.

### DEEP_LINK
Open hosted form with:
- tailored resume ready;
- cover letter ready;
- answer pack;
- checklist.

### MANUAL
Track externally completed applications with no form automation.

### DISABLED
Use when technical/legal/security conditions are not satisfied.

## 3. Capability resolver

```text
Job
 ↓
Source Capability Registry
 ↓
Official authorized submission?
 ├─ yes -> OFFICIAL_API
 └─ no
     ↓
Browser assist allowed?
 ├─ yes -> ASSISTED_EXTENSION
 └─ no
     ↓
Hosted application URL?
 ├─ yes -> DEEP_LINK
 └─ no -> MANUAL/DISABLED
```

## 4. Application policy

User-level settings:
- manual only;
- assisted;
- official auto-apply after review;
- official auto-apply within rules.

Rule dimensions:
- minimum match;
- excluded companies;
- excluded job titles;
- salary floor;
- location;
- remote/hybrid;
- employment type;
- visa/work authorization;
- seniority;
- daily cap.

## 5. Application Kit

Immutable snapshot associated with an attempt:
- job version;
- profile version;
- resume version;
- cover letter;
- answer set;
- generated messages;
- match evidence;
- user confirmations;
- timestamp.

## 6. Form field classification

### Safe auto-fill
- name;
- email;
- phone;
- city;
- portfolio URLs;
- confirmed education;
- confirmed experience.

### Confirm once / reusable
- salary expectation;
- notice period;
- work authorization;
- relocation;
- travel;
- start date.

### Always confirm when asked
- self-identification/demographics;
- disability/health;
- race/ethnicity;
- gender;
- veteran status;
- criminal/legal declarations;
- conflict of interest;
- background checks;
- sponsorship if ambiguous;
- questions with legal attestation.

## 7. Answer engine

Priority order:
1. exact confirmed saved answer;
2. structured profile fact;
3. grounded generation from Professional Truth Store;
4. ask user.

Never guess.

## 8. Submission idempotency

Suggested key:
`hash(user_id + canonical_job_id + connector + profile_version + intended_action)`

A retry cannot create a second application unless the connector explicitly requires a new attempt and user confirms.

## 9. Failure taxonomy

- AUTH_REQUIRED
- PARTNER_ACCESS_REQUIRED
- JOB_CLOSED
- JOB_CHANGED
- FORM_CHANGED
- MISSING_REQUIRED_FIELD
- USER_INPUT_REQUIRED
- CAPTCHA
- RATE_LIMITED
- PROVIDER_ERROR
- NETWORK_ERROR
- DUPLICATE_APPLICATION
- COMPLIANCE_BLOCK
- UNSUPPORTED

Each error maps to:
- retry;
- fallback mode;
- user action;
- disable connector;
- escalation.

## 10. Anti-fragility

A connector can fail independently. On failure:
- preserve generated kit;
- downgrade to assisted/deep-link;
- never discard user work;
- never mark Applied unless submission evidence exists.

## 11. Browser extension security

- Manifest V3.
- least permissions.
- no ATS credentials.
- no LLM API keys.
- origin allowlist.
- short-lived backend tokens.
- explicit per-site activation.
- do not scrape unrelated page data.
- do not silently click final submit in V1.x.

## 12. Learning from AutoApply

Useful code/logic to study:
- per-platform applier interface;
- form selectors;
- CAPTCHA detection;
- resume upload;
- common field filling;
- scoring gate;
- review mode;
- lifecycle persistence.

What not to copy as product assumption:
- universal LinkedIn/Indeed headless submission;
- selector stability as guarantee;
- automation that bypasses platform controls.

## 13. Success criteria

Apply Engine is successful when:
- user fills less repetitive data;
- application artifacts are correct;
- duplicates approach zero;
- failures degrade gracefully;
- every applied status has evidence;
- unsupported platforms still benefit from a prepared kit.
