# Operator-facing UI polish

**Always-on intent:** when adding or planning any operator-facing UI (Operator Console
pages, drawers, tables, chips, toasts), match the existing product look-and-feel with
polished, refined, contemporary patterns. Reuse design-system primitives and tokens.
Never ship ad-hoc, unfinished, or prototype-looking (“noob”) UI.

Source: Issue #194 jam (2026-07-26). Also recorded as user-preferences Design #27 and
`plan-execution-readiness.mdc` item 11 (wire those `.mdc` entries in the same PR that
first ships UI under this rule if they are not already present).

## Plan gate

Any plan that introduces or changes operator-facing UI must:

1. **Cite** the existing components/tokens it will reuse (e.g. Operator Console
   `Badge`, `DataTable`, shadcn/Tailwind muted/border/radius classes — not a new visual
   language).
2. **Specify** interaction states: hover, focus-visible, pending/disabled, mobile tap
   targets (~44px).
3. **Treat visual polish as acceptance evidence** — hosted screenshots are a polish bar,
   not only a functional bar. If it looks bolted-on, rework before merge.

## Console defaults (current stack)

| Concern | Reuse |
|---|---|
| Components | `apps/operator-console/components/ui/*` (shadcn), `components/tables/DataTable.tsx` |
| Status chips | `Badge` variants already used for status/source columns |
| Actions | `useConsoleAction` + `ActionToast` (restock / tip-exemptions pattern) |
| Tone | Short methodology notes like `/inventory` runway blurb — precise, not chatty |

## Anti-patterns

- One-off inline styles / rainbow buttons unrelated to the console theme
- Debug dumps, unlabeled icon-only controls, monospace walls of raw SQL in the main UI
- Desktop-only controls that break at 390px width
- “Temporary” prototype UI left in a merged PR
