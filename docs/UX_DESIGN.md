# UX & Design System Direction — Tap Vaga

## 1. Product UX principles

1. **Action over dashboards:** every screen should make next action obvious.
2. **Explain AI:** show why a match or recommendation exists.
3. **Progressive disclosure:** simple first, detail on demand.
4. **Calm automation:** proactive without becoming noisy.
5. **Human control:** destructive/external actions are visible.
6. **Career truth:** avoid interface that encourages embellishment.
7. **Mobile-native:** thumb reach, deep links, push, share, camera/files.
8. **No "AI aesthetic":** avoid excessive gradients, magic sparkles and vague copy.

## 2. Visual direction

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

## 3. Core components

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

## 4. Match UX

Never show only "91%".

Show:
- score;
- top reasons;
- blockers;
- matched skills;
- missing requirements;
- confidence;
- "como calculamos".

## 5. AI generated artifact UX

For resume/cover letter/answers:
- diff where relevant;
- source facts;
- "não inventar" guard;
- accept/edit/regenerate;
- version history.

## 6. Apply UX

Primary CTA adapts to capability:
- "Candidatar agora" — official API;
- "Preencher candidatura" — extension;
- "Abrir candidatura" — deep link;
- "Marcar como candidatei" — manual.

The UI must never imply an application was sent when it was merely prepared.

## 7. WhatsApp -> app handoff

Notification/card should deep-link directly to entity/action, not home.

## 8. Accessibility

- WCAG AA target for web;
- native accessibility labels;
- dynamic text;
- high contrast;
- keyboard support on web;
- reduce motion preference.

## 9. Design documentation workflow

Before large implementation:
- information architecture;
- low-fi flows;
- design tokens;
- component inventory;
- states/edge cases;
- screen acceptance criteria.

Design decisions that affect business behavior must be reflected in PRD/feature docs.
