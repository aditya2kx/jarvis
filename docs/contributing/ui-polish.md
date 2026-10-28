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

## Design principles

The operator's UX principles — canonical list. Apply them to every console change, and check a
new design against each one before showing it. **Evolve in place:** add, reword or retire a
principle in a PR that cites the conversation or issue where it came from. Never fork a copy into
another doc; link here instead.

| # | Principle | In practice | Source |
|---|---|---|---|
| D1 | **Enhance an existing view before adding a new one.** Every extra page, tab, section or chart adds reading and upkeep cost. | Unavailability lives in the Coverage chart, not a separate Availability page or tabs. | #427 |
| D2 | **One way to do a thing.** Two paths to the same action are confusing. | Edit unavailability via the person's name; lanes only inspect. | #427 |
| D3 | **New UX never regresses old capabilities.** Before showing anything, re-check every existing interaction, including on iPhone with real touch, not only mouse-emulated taps. | Tapping a shift still shows its start/end tooltip after unavailability landed on the lanes. | #427 |
| D4 | **Convey each fact once.** No table, sentence or label that restates what a chart already shows; keep only rows/labels that add an action or a fact the visual can't. | Saved-shifts table lists only shifts needing a pick or an ADP fix; no narration under the headcount ribbon. | #427 |
| D5 | **One meaning per visual encoding, clearly distinct from the others.** A clear encoding replaces text labels and saves space. | Hatching = unavailable only (no label); shifts = tinted outlines. | #427 |
| D6 | **Same kind of thing, same interaction.** | Any lane — shift, open shift, unavailability, free person — taps to the same tooltip. | #427 |
| D7 | **Efficient bulk input, batched apply.** One selection covers many dates or a recurrence; stage changes, then apply them to the external system in one run. | Unavailability: pick days / repeats weekly for one person; review tray → one ADP run. | #427 |
| D8 | **Self-explanatory wording.** Plain language over shorthand; if the operator has to ask what a label means, rename it. | "Off all day" prompted a rename to plain unavailability wording. | #427 |
| D9 | **Complete picture, compact default.** Show everyone relevant, but collapse the secondary part so the default stays short. | Coverage day: collapsed "N not scheduled · M free" group lists every active person. | #427 |
| D10 | **The external system is the source of truth.** The console mirrors ADP/Square and writes back through them; it never keeps a competing copy. | Unavailability reads from and applies to ADP. | #427 |

## Anti-patterns

- One-off inline styles / rainbow buttons unrelated to the console theme
- Debug dumps, unlabeled icon-only controls, monospace walls of raw SQL in the main UI
- Desktop-only controls that break at 390px width
- “Temporary” prototype UI left in a merged PR
- Breaking any principle under **Design principles** below
