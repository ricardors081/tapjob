"""Career Profile & Professional Truth Store (docs/adr/0010-career-profile-truth-store.md, D-052).

A `CareerProfile` is the one canonical, versioned professional profile per TapJob `User`.
A `ProfileFact` is an immutable, provenance-bearing professional claim (experience, education,
skill, certification, language, project, link, preference, work authorization) that a
`TruthSnapshot` (`service.get_truth_snapshot`) later exposes to any consumer -- Resume Import
review, matching, tailored resumes, application answers, the Career Agent, WhatsApp -- as the ONLY
allowed source of grounding for generated external material (docs/AI_AGENT.md §6, AGENTS.md §6).

Identity invariant carried over from WP1.1: this module never accepts a client-supplied `user_id`.
Every `/me/...` route derives it from `tapjob_api.identity.dependencies.get_current_user`, the same
verified-token trust boundary WP1.1 established.
"""
