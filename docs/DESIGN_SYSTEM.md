# Design System & Tokens — TapJob

**Status:** Foundation standard  
**Initial market:** Brazil  
**Canonical rule:** screens and components consume tokens; they do not invent visual constants.

This document defines how TapJob must structure visual decisions so that a rebrand, theme change or accessibility adjustment can be made centrally instead of editing every screen.

## 1. Core principle

Do not scatter values such as:

```ts
color: "#2563EB"
fontSize: 16
borderRadius: 12
padding: 16
```

through feature code.

Instead, feature code consumes semantic tokens:

```ts
backgroundColor: tokens.color.action.primary.background
color: tokens.color.action.primary.foreground
fontSize: tokens.typography.body.md.fontSize
borderRadius: tokens.radius.md
padding: tokens.spacing.md
```

The token names express **meaning**, not current appearance.

Bad:
- `blue500`
- `greenButton`
- `font16`
- `radius12`

Preferred:
- `color.action.primary.background`
- `color.feedback.success.foreground`
- `typography.body.md`
- `radius.md`

A future rebrand changes token values, not business screens.

## 2. Token standard

TapJob should use a DTCG-compatible design-token source when practical.

Target source of truth:

```text
packages/
  design-tokens/
    tokens.json
    themes/
      light.json
      dark.json
    generated/
      native.ts
      web.css
```

`tokens.json` is canonical. Generated platform files must not be hand-edited.

A token build step may later use Style Dictionary, Terrazzo or equivalent if justified. Do not add token tooling until WP0.1/WP0.2 chooses the implementation.

## 3. Initial visual baseline

Initial values are centralized tokens and may evolve without feature-screen rewrites.

### Brand
- primary: `#2563EB`
- primary strong: `#1D4ED8`
- accent orange: `#F97316`

### Semantic
- success/verified: `#16A34A`
- warning: `#D97706`
- error/rejected: `#DC2626`
- info: `#0284C7`

### Light surfaces
- canvas: `#F8FAFC`
- surface: `#FFFFFF`
- text primary: `#0F172A`
- text secondary: `#475569`
- border: `#E2E8F0`

### Typography
Initial product typeface: **Inter**.

### Spacing
4-point foundation with a finite semantic scale.

### Radius
Initial scale:
- sm: 8;
- md: 12;
- lg: 16;
- xl: 24;
- full: pill/circle.

### Motion
Initial duration direction:
- fast: ~160 ms;
- standard: ~220 ms;
- emphasis: ~320 ms.

### Iconography
Initial primary family: **Lucide**.

No screen may depend on these raw values directly; components consume semantic tokens.

## 4. Token layers

### 3.1 Primitive tokens

Raw palette and scale values. Feature code should almost never use these directly.

Examples:

```text
color.palette.blue.500
color.palette.green.600
color.palette.red.600
space.100
space.200
font.size.300
radius.200
```

### 3.2 Semantic tokens

Meaningful roles used by application code.

Examples:

```text
color.background.canvas
color.background.surface
color.background.elevated

color.text.primary
color.text.secondary
color.text.muted
color.text.inverse

color.border.default
color.border.strong
color.border.focus

color.action.primary.background
color.action.primary.foreground
color.action.primary.hover
color.action.secondary.background

color.feedback.success.background
color.feedback.success.foreground
color.feedback.warning.background
color.feedback.warning.foreground
color.feedback.error.background
color.feedback.error.foreground
color.feedback.info.background
color.feedback.info.foreground

color.status.applied
color.status.interview
color.status.offer
color.status.rejected
color.status.pending
```

Semantic tokens may alias primitive tokens.

### 3.3 Component tokens

Only create component tokens when a component genuinely needs a stable contract beyond general semantic tokens.

Examples:

```text
component.jobCard.padding
component.jobCard.radius
component.bottomNav.height
component.scoreBadge.size
```

Do not create one-off component tokens to hide arbitrary values.

## 5. Required token categories

### Color
- brand;
- canvas/surface/elevated;
- text;
- border;
- action;
- feedback;
- application status;
- focus;
- disabled;
- overlay.

### Typography
Each style should define:
- font family;
- weight;
- size;
- line height;
- letter spacing.

Semantic typography examples:
- `display.lg`;
- `heading.lg`;
- `heading.md`;
- `body.lg`;
- `body.md`;
- `body.sm`;
- `label.md`;
- `caption`.

### Spacing
Use a finite scale.

Example direction:
- `xs`;
- `sm`;
- `md`;
- `lg`;
- `xl`;
- `2xl`.

Feature code must not introduce arbitrary `13px`, `17px`, etc. without documented justification.

### Radius
Finite scale:
- none;
- sm;
- md;
- lg;
- xl;
- full.

### Elevation/shadow
Use semantic elevation levels instead of custom shadows per screen.

### Motion
Centralize:
- durations;
- easing;
- spring presets;
- reduced-motion behavior.

Motion is functional feedback, not decoration.

### Iconography
- one primary icon family per surface;
- canonical icon sizes;
- semantic icon mapping where appropriate;
- no random mixture of icon libraries inside the same product surface.

### Layout
Centralize:
- content max widths;
- screen gutters;
- safe-area behavior;
- card gaps;
- bottom-navigation measurements;
- touch target minimums.

## 6. Brand configuration

Brand identity must not be duplicated across screens/templates.

Target structure:

```text
packages/
  product-config/
    product.json
```

Example:

```json
{
  "productName": "TapJob",
  "shortName": "TapJob",
  "legalName": null,
  "defaultLocale": "pt-BR",
  "defaultCurrency": "BRL",
  "supportEmail": null,
  "domains": {
    "web": null,
    "api": null
  }
}
```

The exact schema is finalized in WP0.1/WP0.2.

Use cases:
- app header/title;
- notification templates;
- emails;
- PDF metadata;
- deep-link copy;
- support screens;
- build metadata.

Do not hardcode `"TapJob"` throughout runtime code.

Repository/documentation titles may naturally contain the project name; runtime UI/code must consume central brand configuration.

## 7. Theme strategy

Support token aliases for:
- light;
- dark;
- high-contrast/accessibility variants when needed.

Initial product can remain light-first, but components must not assume white backgrounds or black text directly.

Bad:

```ts
backgroundColor: "#FFFFFF"
```

Preferred:

```ts
backgroundColor: tokens.color.background.canvas
```

## 8. Semantic colors are not brand colors

Brand and status meanings are separate.

Example:
- verified/success uses semantic success token;
- rejected/error uses semantic error token;
- warning uses semantic warning token;
- primary CTA uses brand/action token.

Changing the brand from blue to another color must not make "success" suddenly inherit the brand color.

Do not communicate status using color alone; include text/icon/state for accessibility.

## 9. Component architecture

Prefer reusable primitives and composed components.

Target direction:

```text
apps/mobile/src/
  components/
    primitives/
      Button/
      Text/
      Input/
      Card/
      Badge/
      Sheet/
      Divider/
    domain/
      JobCard/
      MatchBreakdown/
      ApplicationCard/
      ResumeCard/
      InterviewCard/
```

A domain component may compose primitives but should not recreate spacing/color/typography rules.

## 10. No inline design drift

Merge-blocking patterns after the design-system package exists:
- new raw hex/rgb colors in feature screens;
- arbitrary font sizes;
- arbitrary spacing/radius values;
- duplicate buttons/cards instead of reusable primitives;
- direct font-family strings outside token/config modules;
- status colors hardcoded inside feature components.

Lint/custom checks may enforce these rules later.

## 11. Accessibility baseline

- WCAG AA target for web surfaces;
- native accessibility labels/roles;
- dynamic text where platform allows;
- minimum touch target rules;
- focus visibility;
- sufficient contrast;
- reduced-motion support;
- status is never color-only.

Accessibility tokens/variants should be possible without rewriting features.

## 12. Localization-aware design

Initial locale is `pt-BR`, but layouts must allow:
- longer translated strings;
- currency formatting;
- date formatting;
- pluralization;
- different number formats.

Do not bake Portuguese text width assumptions into layout.

## 13. Design review gate

A substantial UI PR should verify:
- tokens used instead of magic visual values;
- dark/theme-safe semantics where applicable;
- loading/empty/error/disabled states;
- accessibility labels;
- touch targets;
- text overflow;
- keyboard behavior;
- reduced-motion behavior where animation exists;
- screenshots or visual evidence for meaningful UI changes.

## 14. Rebranding rule

A normal brand refresh should primarily require changes to:
- product config;
- design tokens;
- assets/logo;
- store metadata.

It should **not** require editing dozens of feature screens.

If a brand change requires widespread screen edits, the design-system abstraction has failed.