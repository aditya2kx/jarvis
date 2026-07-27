# Icon-rail left sidebar (Operator Console) — Issue #200

Evidence tier: sandbox-e2e
scenario: operator-console-sidebar-icon-rail

## Jam / §4 (approved in chat — “build it”)

- **Collapse style:** Option B — icon rail (not full hide). Collapsed = icons only, still navigable; toggle expands back to labels.
- **`< md`:** keep existing `MobileNav` Sheet unchanged.
- **`md+`:** docked sidebar with animated width (`w-60` ↔ ~`w-14`).
- **Auto:** default collapsed when viewport `< lg` (1024px); persist override in `localStorage` key `oc_sidebar_collapsed`.
- **Feature flag:** none — additive shell UX; cannot silently produce wrong numbers (flag decision: no new flag needed).
- **Model routing:** Sonnet for implement/UI/tests; Composer for docs-only touch-ups.

### Per-scenario evidence (PR §4) — hosted https screenshots required (G5)

Happy path + failure/edge + legacy; pass criterion = each screenshot URL renders in PR §4 and unit tests green.

1. **Happy path — E1:** Desktop expanded (≥ lg) — full labels + group headers.
2. **Happy path — E2/E3:** Collapse to icon rail; click icon navigates (e.g. Sales); active state visible.
3. **Happy path — E4:** Expand restores labels.
4. **Failure / edge — E5:** ~900px (`md`–`lg`) loads collapsed; can expand.
5. **Legacy — E6:** 390px mobile — hamburger Sheet still works; no stuck desktop rail.
6. **E7–E9:** persistence + a11y (`aria-expanded`, icon `aria-label`) + `verify.py --full` + `ARCHITECTURE.md` §9.
7. Post-merge: spot-check prod/review console at desktop / ~900px / phone.

## Citations

- `apps/operator-console/components/shell/Sidebar.tsx` lines 8–40 (`Sidebar`, `hidden md:flex w-60`)
- `apps/operator-console/components/shell/MobileNav.tsx` lines 22–70 (`MobileNav` Sheet `< md`)
- `apps/operator-console/components/shell/Topbar.tsx` lines 25–41 (`MobileNav` + title)
- `apps/operator-console/components/shell/nav-items.ts` lines 29–56 (`NAV_GROUPS` + icons)
- `apps/operator-console/app/layout.tsx` lines 46–51 (shell flex: Sidebar + Topbar + main)
- `apps/operator-console/components/shell/ThemeToggle.tsx` lines 7–40 (`oc_theme` localStorage pattern to mirror)
- `docs/operator-console/ARCHITECTURE.md` lines 317–336 (§9 Responsive design — update)
- Docs lock-step: `docs/operator-console/ARCHITECTURE.md` §9; run `python3 scripts/check_doc_freshness.py`. No RUNBOOK.md (pipeline unchanged). CONTRIBUTING.md §4 for PR evidence. PROGRESS.md only via follow-up retro PR if needed.

## Stubs

```ts
// apps/operator-console/components/shell/useSidebarCollapsed.ts
export const SIDEBAR_STORAGE_KEY = "oc_sidebar_collapsed";
export const LG_MIN_PX = 1024;

export function readStoredCollapsed(): boolean | null
export function writeStoredCollapsed(collapsed: boolean): void
export function defaultCollapsedForWidth(widthPx: number): boolean
// width < LG_MIN_PX → true; else false

export function useSidebarCollapsed(): {
  collapsed: boolean;
  setCollapsed: (next: boolean) => void;
  toggle: () => void;
  ready: boolean;
}
```

```tsx
// Sidebar.tsx — collapsed rail
<nav className={cn(
  "hidden shrink-0 border-r … md:flex md:flex-col transition-[width] duration-200",
  collapsed ? "w-14 px-2" : "w-60 px-3",
)}>
  <Button aria-expanded={!collapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} …>
    <PanelLeftClose | PanelLeft />
  </Button>
  {/* group labels: only when !collapsed */}
  {/* links: icon always; label span hidden when collapsed; Tooltip when collapsed */}
</nav>
```

```bash
cd apps/operator-console && npx vitest run __tests__/sidebar-collapse.test.ts __tests__/sidebar.test.tsx
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full
gh pr create --base main --head fix/i200-make-left-side-panel-responsive-to …
```

## Invariants

- Must not break `< md` `MobileNav` Sheet navigation (legacy invariant).
- `NAV_GROUPS` remains single source of truth — never duplicate nav list.
- Prefer localStorage persistence like theme; no hardcoding store IDs / sheet IDs.
- No BHAGA pipeline / cents / America/Chicago side effects (shell-only; sandbox isolation N/A for numbers).
- Idempotent toggle — re-click restores prior expanded width; no layout flash after `ready`.

## Milestone 1 — Collapse state hook + unit tests (Sonnet)

Add `useSidebarCollapsed.ts` (+ pure helpers) mirroring `ThemeToggle` storage pattern; vitest for default-by-width, storage round-trip, toggle.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/sidebar-collapse.test.ts
```

## Milestone 2 — Sidebar UI icon rail + a11y (Sonnet)

Update `Sidebar.tsx` for animated width, icon-only mode with Tooltip/`aria-label`, collapse toggle; keep `MobileNav` as-is. Optional thin client wrapper if layout stays Server Component.

**Verify:**
```bash
cd apps/operator-console && npx vitest run __tests__/sidebar.test.tsx
cd apps/operator-console && npm run lint
```

## Milestone 3 — Docs + full verify + PR evidence (Composer/Sonnet)

Update `ARCHITECTURE.md` §9; `check_doc_freshness.py`; `verify.py --full`; open PR with §4 E1–E9; babysit (never self-merge; operator squash-merges). PR mechanics: one branch `fix/i200-make-left-side-panel-responsive-to`, `gh pr create --base main`, bot account, babysit skill, never self-merge.

**Verify:**
```bash
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full
```

## Branch / PR mechanics

- Branch: `fix/i200-make-left-side-panel-responsive-to` (Issue #200)
- `gh pr create --base main` — never self-merge; operator merge only
- Babysit via `pr_triage.py` after push; reply every thread
- Cost: `pr_cost_ledger.py bind-pr` + `sync` after PR exists
