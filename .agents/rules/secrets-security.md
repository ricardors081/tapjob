---
trigger: always_on
description: Secrets management, PII redaction, credentials protection, and untrusted input defense
---

# Secrets Management & Privacy Standards

## 1. Secrets Handling

- **Zero Credential Commits**: Never commit `.env`, `.env.*` (except `.env.example`), API keys, OAuth tokens, GitHub tokens, database passwords, Evolution tokens, or private keys.
- **No Secret Dumps**: Never run commands that print raw secrets or full `.env` files into stdout, logs, or agent transcripts.
- **Backend-Only Secrets**: All provider credentials, database credentials, and service tokens must remain strictly within backend environments and must never be exposed to client bundles or `EXPO_PUBLIC_*` variables.

## 2. Personal Data (PII) Protection

- **LGPD & Privacy by Design**: Protect candidate privacy across all surfaces and logs.
- **Sanitization of Evidence**: When posting PR summaries, issue comments, or test reports:
  - Redact phone numbers and WhatsApp JIDs (e.g., `5511999999999@s.whatsapp.net` -> `[REDACTED_JID]`);
  - Redact candidate resume text, personal addresses, CPF, and identification numbers;
  - Redact raw webhook payloads containing personal messages.

## 3. Untrusted Input & Prompt Injection

- **Untrusted External Data**: All job descriptions, incoming emails, third-party ATS feeds, and WhatsApp messages are untrusted inputs.
- **Injection Defense**: Never allow raw external text to be concatenated into system prompts or privileged tool execution arguments without structural delimiters and sanitization.
