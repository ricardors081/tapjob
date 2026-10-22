## Objective
Implement practical product validation, natural language intent routing, controlled live canary tooling, human acceptance testing guidelines, and enhanced operator inspection for the TapJob WhatsApp controlled pilot (`WP-PILOT-2`).
Includes Corrective Hardening Rounds 1 and 2 resolving independent review findings (per-turn database domain assertions, fail-closed live verification, measurable invariant checks, layered NLU with `ConversationContext`, and context-aware URL classification).

## Scope
1. **Conversational Intent Router**: Implemented a hybrid deterministic conversational intent router with contextual disambiguation and safe clarification fallback in `services/api/src/tapjob_api/messaging/natural_language.py`. Context-aware disambiguation handles affirmative responses ("bora", "quero", "essa parece boa") against active conversation state.
2. **Context-Aware URL Classification**: Implemented `classify_url_intent(url, text)` distinguishing ATS jobs (`JOB_URL_INTAKE`), candidate profiles/portfolios (`TEXT_EXPERIENCE`), privacy policies (`PRIVACY_INQUIRY`), and generic URLs requesting safe clarification (`CLARIFICATION_NEEDED`).
3. **Product Journey Matrix**: Comprehensive documentation of 22 candidate journeys in Brazilian Portuguese with required context, actions, expected responses, and evidence.
4. **Controlled Live Canary Validation with Per-Turn Assertions**: Automated validator (`tools/ops/live_canary_test.py`) with strict recipient allowlist gating (`ALLOWED_RECIPIENTS = {"573137848445", "639568348733"}`), zero third-party sends, 10-turn multi-turn execution, bot reply roundtrip polling on canary client, content assertions, per-turn database domain effect assertions (inbound envelope processed, routed intent match, profile version/facts, target job, application status, reminders count), fail-closed verification on live execution, and measurable database invariant verification (`DUPLICATE_SIDE_EFFECTS = 0`, `RECURSIVE_WHATSAPP_LOOPS = 0`, `THIRD_PARTY_SENDS = 0`).
5. **Human Acceptance Test Guide**: Clear, non-scripted test scenarios for human testers on WhatsApp (`docs/pilots/HUMAN_ACCEPTANCE_TEST_GUIDE.md`).
6. **Pilot Operator Inspection CLI**: Enhanced `tools/ops/pilot_inspect.py` (`--phone`) to inspect candidate profile facts summary, active target job, and latest application state with strict LGPD PII masking. Architectural evaluation confirming `PILOT_CONSOLE_REQUIRED = NO`.

## Changes
- `docs/pilots/WP_PILOT_2_JOURNEY_MATRIX.md`: 22 end-to-end product journeys documented.
- `docs/pilots/HUMAN_ACCEPTANCE_TEST_GUIDE.md`: Spontaneous human testing guide for pilot users.
- `services/api/src/tapjob_api/messaging/natural_language.py`: Hybrid deterministic conversational intent router with contextual disambiguation (`ConversationContext`, `SemanticInterpretation`, `IntentConfidence`, `classify_url_intent`, `extract_urls`, `interpret_conversational_message`).
- `services/api/src/tapjob_api/messaging/orchestration.py`: Integrated conversation context resolution (`resolve_user_conversation_context`), envelope intent observability, and clarification handling.
- `services/api/tests/messaging/test_pilot_natural_language.py`: 134 test cases verifying natural language intent classification, contextual interpretation, URL classification, and single-item fact operations.
- `tools/ops/live_canary_test.py`: Controlled live canary runner with 10-turn roundtrip reply polling, per-turn domain effect assertions (30 total assertions evaluated), fail-closed live verification, and measurable invariant checks.
- `tools/ops/test_live_canary_test.py`: 12 unit tests verifying recipient allowlist gate, message extraction, turn reply and domain assertions, fail-closed verification, DB verifier, and dry-run execution.
- `tools/ops/pilot_inspect.py`: Added profile facts summary, target job, and latest application state inspection to `--phone`, fixed SQL join on `channel_identity_links` and `channel_identities`.
- `tools/ops/test_pilot_inspect.py`: 22 unit tests for participant profile and application inspection.
- `tools/agent_guard/validate_governance.py`: Accepted `AF-2 = CLOSED_AND_VERIFIED` in governance validation.
- `docs/PROJECT_STATE.md`: Recorded PR #19 closeout and WP-PILOT-2 implementation status with exact gate flags.

## Tests
- `uv run pytest services/api/tests/messaging/`: 248/248 passed (100% green).
- `uv run pytest`: 1014/1014 passed, 0 failures across the whole monorepo.
- `uv run pytest tools/ops`: 34/34 passed (live canary tests + pilot inspect tests).
- `uv run python -m unittest discover tools/`: 88/88 passed.

## Security & Privacy
- **Strict Recipient Allowlist**: Hard-coded gate in `live_canary_test.py` strictly restricts destinations to canary instances (`573137848445` and `639568348733`). Any other recipient raises `RuntimeError`.
- **Third-Party Sends**: Exactly 0 (`THIRD_PARTY_SENDS = 0`).
- **LGPD & PII Protection**: Strict regex masking of phones, JIDs, emails, and credentials across operator outputs.
- **Truth Store Invariant**: Resumes and application kits remain strictly grounded in user-confirmed facts. Zero hallucinations or fabricated data.
- **Operator Console Safety**: `PILOT_CONSOLE_REQUIRED = NO` avoids creating an exposed web surface; CLI + JSON satisfies all operator monitoring needs.

## External Impact
- No production mutation.
- Staging Evolution API canary instances utilized under strict allowlist.
- EasyPanel staging environment configuration untouched.
- Mobile client remains paused (`MOBILE_STILL_PAUSED = YES`).
- Agent Factory AF-3 remains unstarted (`AF3_STILL_NOT_AUTHORIZED = YES`).

## Evidence
- Local governance verification: `[PASS] TapJob Governance Validation Passed`.
- Linting: `uv run ruff check .` passed cleanly.
- Formatting: `uv run ruff format --check .` passed cleanly.
- Type checking: `uv run python -m mypy` passed.
- Git hygiene: `git diff --check` passed cleanly.

## Rollback Strategy
Revert the commit or branch `feat/whatsapp-pilot-practical-validation`. No schema migrations were added; Career Core and channel contracts remain fully backward compatible.
