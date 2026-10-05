"""WP1.1 — Identity & User Account (docs/adr/0009-identity-auth-user-account.md, D-051).

The TapJob internal ``User`` UUID is the one canonical identity; a Firebase UID is an
``ExternalIdentity`` linked to it, never the primary key itself -- a User may exist with zero
external identities (e.g. a future WhatsApp-first onboarding), and gains a Firebase identity only
through the explicit bootstrap flow (service.bootstrap_user), never through an implicit email
match (docs/FOUNDATION_DECISIONS.md "Firebase Authentication").
"""
