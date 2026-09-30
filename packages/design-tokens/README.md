# @tapjob/design-tokens

Canonical visual tokens (D-023, `docs/DESIGN_SYSTEM.md`).

| Path | Role |
|---|---|
| `tokens.json` | Canonical DTCG source: `primitive` palette/scales plus theme-independent semantic tokens (radius, motion, typography). |
| `themes/light.json` | Light-theme semantic colors aliasing primitives. |
| `scripts/build.mjs` | Dependency-free generator. |
| `generated/native.ts` | Generated React Native output. **Never edit by hand.** |

```bash
npm run tokens:build   # regenerate after editing tokens.json / themes
npm run tokens:check   # CI: fails if generated output is stale
```

## Format notes

- Sources follow the stable [DTCG Format 2025.10](https://www.designtokens.org/tr/2025.10/format/) and declare its [official JSON Schema](https://www.designtokens.org/schemas/2025.10/format.json) in `$schema` (editor hint only; no validation dependency is added).
- DTCG groups, `$type` inheritance and whole-value aliases (`"{primitive.color.blue.600}"`) are supported.
- `dimension`/`duration` use the DTCG object form (`{ "value": 8, "unit": "px" }`).
- Colors use the [DTCG Color Module 2025.10](https://www.designtokens.org/tr/2025.10/color/) object form: `{ "colorSpace": "srgb", "components": [r, g, b], "hex": "#RRGGBB" }`, with components in `[0, 1]`. The generator supports sRGB only and opaque colors only (`alpha` omitted or `1`). The optional `hex` fallback must match the components. React Native output stays `#RRGGBB` strings.
- The `primitive` group is never exported, so feature code can only consume semantic tokens.

## Not yet defined (design-system workstream)

Only the documented baseline values are encoded. Still pending design decisions, deliberately not invented here:
semantic spacing names (`xs`…`2xl` mapped onto the 4-point `primitive.space` scale), the typography size/line-height scale, feedback background tints, focus/disabled/overlay colors, elevation and a dark theme.
