# UX & Design System Direction — TapJob

## 1. Product UX principles

1. **Action over dashboards:** every screen should make next action obvious.
2. **Explain AI:** show why a match or recommendation exists.
3. **Progressive disclosure:** simple first, detail on demand.
4. **Calm automation:** proactive without becoming noisy.
5. **Human control:** destructive/external actions are visible.
6. **Career truth:** avoid interface that encourages embellishment.
7. **Mobile-native:** thumb reach, deep links, push, share, camera/files.
8. **No "AI aesthetic":** avoid excessive gradients, magic sparkles and vague copy.

## 2. Implementation contract

`docs/DESIGN_SYSTEM.md` is the canonical implementation contract for tokens, themes, typography, spacing, radius, motion, iconography and brand configuration.

Feature screens must consume semantic tokens/components rather than raw hex colors, font sizes or arbitrary spacing values. A rebrand must primarily change tokens/config/assets, not dozens of screens.

## 3. Visual direction

Not yet brand-final.

Baseline:
- clean;
- light-first;
- strong typography;
- neutral surfaces;
- one primary brand color to be decided;
- semantic colors separate from brand.

Semantic roles:
- success/verified;
- warning;
- error/rejected;
- info;
- neutral;
- pending.

Do not encode application status by color alone.

## 4. Core components

- AppShell / BottomTabs
- JobCard
- MatchBadge
- ScoreBreakdown
- StatusChip
- ApplicationCard
- Timeline
- ResumeCard
- DocumentPreview
- AIActionCard
- AgentMessage
- ConfirmationSheet
- TaskCard
- InterviewCard
- NotificationPreference
- EmptyState
- Skeleton
- ErrorState
- ConsentDialog

## 5. Match UX

Never show only "91%".

Show:
- score;
- top reasons;
- blockers;
- matched skills;
- missing requirements;
- confidence;
- "como calculamos".

## 6. AI generated artifact UX

For resume/cover letter/answers:
- diff where relevant;
- source facts;
- "não inventar" guard;
- accept/edit/regenerate;
- version history.

## 7. Apply UX

Primary CTA adapts to capability:
- "Candidatar agora" — official API;
- "Preencher candidatura" — extension;
- "Abrir candidatura" — deep link;
- "Marcar como candidatei" — manual.

The UI must never imply an application was sent when it was merely prepared.

## 8. WhatsApp -> app handoff

Notification/card should deep-link directly to entity/action, not home.

## 9. Accessibility

- WCAG AA target for web;
- native accessibility labels;
- dynamic text;
- high contrast;
- keyboard support on web;
- reduce motion preference.

## 10. Design documentation workflow

Before large implementation:
- information architecture;
- low-fi flows;
- design tokens;
- component inventory;
- states/edge cases;
- screen acceptance criteria.

Design decisions that affect business behavior must be reflected in PRD/feature docs.