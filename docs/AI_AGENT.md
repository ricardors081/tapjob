# Career Agent — TapJob

## 1. Role

The Career Agent is a stateful tool-using assistant over the user's Career Core.

It is not a generic chatbot.

## 2. Core capabilities

- understand career goals;
- answer questions from profile/history;
- search/filter jobs;
- explain match;
- create/update watchlists;
- review resume;
- tailor resume;
- generate application kit;
- prepare application;
- update tracker;
- create follow-up tasks;
- prepare interview;
- summarize pipeline;
- analyze trends;
- notify proactively.

## 3. Tool categories

### Read tools
- get_profile
- get_preferences
- search_jobs
- get_job
- get_match
- list_applications
- get_application
- list_interviews
- get_resume
- get_analytics

### Write tools
- update_profile_fact
- save_job
- create_resume_version
- prepare_application_kit
- change_application_status
- create_task
- schedule_reminder
- add_interview_note
- create_contact

### High-impact tools
- submit_official_application
- send_message
- send_email
- delete_data
- connect_external_account

High-impact tools require explicit policy/confirmation.

## 4. Confirmation model

Actions can be:

- **Read-only:** no confirmation.
- **Low-risk write:** confirmation may be implicit in direct user command.
- **External side effect:** explicit confirmation.
- **Sensitive/legal:** explicit field-level confirmation.
- **Destructive:** explicit confirmation plus preview.

## 5. Memory

### Durable career memory
Stored in product database:
- confirmed facts;
- preferences;
- application history;
- interview stories;
- reusable answers.

### Conversation memory
Short-lived context scoped to the interaction.

Do not use opaque chat history as sole source for critical professional facts.

## 6. Grounding

Every career claim used in external material must reference a confirmed fact ID internally.

Generated artifact metadata should include:
- profile_version;
- source facts;
- prompt/version;
- model;
- generated_at.

## 7. Prompt injection defense

Treat as untrusted:
- job descriptions;
- company pages;
- e-mail bodies;
- attached documents;
- scraped web content.

Rules:
- external content cannot override tool policy;
- external instructions cannot request secrets;
- tool decisions come from system/product policy;
- sanitize/segment content before LLM use;
- structured output validation.

## 8. Matching explanation

Agent should explain:
- why match is high/low;
- hard blockers;
- evidence from profile;
- missing requirements;
- uncertainty.

Never imply certainty about hiring outcome.

## 9. Proactive behaviors

Opt-in only:
- daily/weekly relevant jobs;
- urgent deadline;
- follow-up due;
- interview upcoming;
- recruiter response;
- application stalled;
- profile/resume stale.

Proactive messages must be throttled and preference-aware.

## 10. Agent surfaces

Same tools/policies across:
- mobile chat;
- WhatsApp;
- future web chat.

Channel-specific adapters handle formatting only.

## 11. AI evaluation

Maintain eval datasets for:
- hallucinated skills;
- incorrect blocker detection;
- resume faithfulness;
- job relevance;
- e-mail event classification;
- action selection;
- prompt injection resistance.

A model upgrade cannot ship solely on subjective quality.

## 12. Cost control

Use tiered intelligence:
1. deterministic filters;
2. cheap parsing/embedding;
3. smaller structured model;
4. expensive model only for high-value reasoning.

Cache results by job/profile version.

## 13. Reference projects

- career-ops: career reasoning, interview prep, patterns.
- Appliable: agent workflow and WhatsApp delivery.
- JobSync: in-app tool-calling assistant and confirmation.
- JobBot: pipeline + alerts.
- Basafy: e-mail classification patterns.