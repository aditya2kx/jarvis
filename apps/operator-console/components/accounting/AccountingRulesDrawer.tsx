"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  dryRunRuleAction,
  revertRuleEvidenceAction,
  setCategoryRuleEnabledAction,
  type RuleDryRun,
  setTaxonomyExcludeAction,
  setTaxonomyNodeEnabledAction,
  upsertTaxonomyNodeAction,
} from "@/app/accounting/actions";
import type { TaxonomyOption } from "@/components/accounting/AccountingLedger";
import { effectiveExclude } from "@/lib/plaid/exclude-accounting";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { okAck } from "@/lib/actions/types";
import { amountSignLabel } from "@/lib/plaid/amount-sign";
import { jobProgressLabel, type CategorizeJob } from "@/lib/plaid/categorize-job-status";
import { duplicateRuleIds } from "@/lib/plaid/rule-commit";
import { formatDollars } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface RuleListItem {
  id: string;
  priority: number;
  match_operator: string;
  match_pattern: string;
  amount_sign: string | null;
  account_mask: string | null;
  from_mask: string | null;
  to_mask: string | null;
  enabled: boolean | null;
  category_id: string;
  subcategory_id: string | null;
  category_label: string | null;
  subcategory_label: string | null;
  applied_count: number;
  notes: string | null;
  updated_at: string | null;
}

/** Lower-case text a rule is searched by (id, pattern, target labels, masks). */
export function ruleSearchText(r: RuleListItem): string {
  return [
    r.id,
    r.match_pattern,
    r.category_label,
    r.subcategory_label,
    r.category_id,
    r.subcategory_id,
    r.from_mask,
    r.to_mask,
    r.account_mask,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function ruleCriteria(r: RuleListItem): string {
  const parts = [
    r.match_pattern ? `${r.match_operator} "${r.match_pattern}"` : "(no name pattern)",
    amountSignLabel(r.amount_sign),
  ];
  if (r.account_mask) parts.push(`account …${r.account_mask}`);
  if (r.from_mask) parts.push(`from …${r.from_mask}`);
  if (r.to_mask) parts.push(`to …${r.to_mask}`);
  return parts.join(" · ");
}

export function AccountingRulesDrawer({
  canWrite,
  ruleCount,
  taxonomy,
  rules,
  jobLive = false,
  onJob,
  onWatch,
}: {
  canWrite: boolean;
  ruleCount: number;
  taxonomy: TaxonomyOption[];
  rules: RuleListItem[];
  /** A Reapply / Revert is running — Revert waits for it. */
  jobLive?: boolean;
  onJob?: (job: CategorizeJob) => void;
  onWatch?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { isPending: pending, stage, error, run } = useConsoleAction();
  const [msg, setMsg] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [parentId, setParentId] = useState("");
  const [newParentLabel, setNewParentLabel] = useState("");
  const [newParentExclude, setNewParentExclude] = useState(false);

  const parents = taxonomy.filter((t) => !t.parent_id);
  const [ruleQuery, setRuleQuery] = useState("");
  const [showOff, setShowOff] = useState(true);
  const [openRule, setOpenRule] = useState<string | null>(null);
  const [dryRuns, setDryRuns] = useState<Record<string, RuleDryRun>>({});
  const [confirmRevert, setConfirmRevert] = useState<string | null>(null);
  const dupOf = useMemo(() => duplicateRuleIds(rules), [rules]);
  const visibleRules = useMemo(() => {
    const needle = ruleQuery.trim().toLowerCase();
    return rules.filter(
      (r) => (showOff || r.enabled !== false) && (!needle || ruleSearchText(r).includes(needle)),
    );
  }, [rules, ruleQuery, showOff]);

  function addSubcategory() {
    if (!newLabel.trim() || !parentId) return;
    const slug = newLabel
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "");
    const id = `${parentId}__${slug}`;
    void run(async () => {
      const ack = await upsertTaxonomyNodeAction({
        id,
        parent_id: parentId,
        slug,
        label: newLabel.trim(),
        enabled: true,
        exclude_from_accounting: null,
      });
      if (!ack.ok) return ack;
      setMsg(`Added subcategory ${newLabel.trim()}`);
      setNewLabel("");
      return okAck({ message: `Added subcategory ${newLabel.trim()}` });
    });
  }

  function addParentCategory() {
    if (!newParentLabel.trim()) return;
    const slug = newParentLabel
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "");
    void run(async () => {
      const ack = await upsertTaxonomyNodeAction({
        id: slug,
        parent_id: null,
        slug,
        label: newParentLabel.trim(),
        enabled: true,
        exclude_from_accounting: newParentExclude ? true : false,
      });
      if (!ack.ok) return ack;
      setMsg(`Added category ${newParentLabel.trim()}`);
      setNewParentLabel("");
      setNewParentExclude(false);
      return okAck({ message: `Added category ${newParentLabel.trim()}` });
    });
  }

  function softDisable(id: string) {
    void run(async () => {
      const ack = await setTaxonomyNodeEnabledAction(id, false);
      if (!ack.ok) return ack;
      setMsg(`Disabled ${id}`);
      return okAck({ message: `Disabled ${id}` });
    });
  }

  function setExclude(id: string, value: boolean | null) {
    void run(async () => {
      const ack = await setTaxonomyExcludeAction(id, value);
      if (!ack.ok) return ack;
      setMsg(`Exclude ${id}: ${value === null ? "inherit" : value ? "yes" : "no"}`);
      return okAck({
        message: `Exclude ${id}: ${value === null ? "inherit" : value ? "yes" : "no"}`,
      });
    });
  }

  function toggleRule(id: string, enabled: boolean) {
    void run(async () => {
      const ack = await setCategoryRuleEnabledAction(id, enabled);
      if (!ack.ok) return ack;
      setMsg(`${enabled ? "Enabled" : "Disabled"} rule ${id}`);
      return okAck({ message: `${enabled ? "Enabled" : "Disabled"} rule ${id}` });
    });
  }

  function dryRun(id: string) {
    void run(
      async () => {
        const ack = await dryRunRuleAction(id);
        if (!ack.ok) return ack;
        const d = ack.data!;
        setDryRuns((prev) => ({ ...prev, [id]: d }));
        setOpenRule(id);
        const message = `Dry-run ${id}: would apply to ${d.would_apply} (now ${d.currently_applied}) · ${d.matches} match`;
        setMsg(message);
        return okAck({ message });
      },
      { saving: "Checking every transaction…" },
    );
  }

  function revert(id: string) {
    setConfirmRevert(null);
    onWatch?.();
    void run(
      async () => {
        const ack = await revertRuleEvidenceAction(id);
        if (!ack.ok) return ack;
        onJob?.(ack.data!.job);
        const message = jobProgressLabel(ack.data!.job);
        setMsg(message);
        return okAck({ message });
      },
      { saving: `Reverting ${id}…` },
    );
  }

  const feedback = error || stage || msg;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button type="button" size="sm" variant="outline">
            Rules ({ruleCount})
          </Button>
        }
      />
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Category rules & taxonomy</SheetTitle>
          <SheetDescription>
            Copilot-style rules. Toggle Exclude from accounting per category/subcategory
            (subcategory inherits parent when set to Inherit). Reapply on the ledger after changes.
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4 pb-6">
          {feedback ? (
            <p className={`text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {feedback}
            </p>
          ) : null}

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Add parent category</h3>
            <input
              className="rounded border bg-background px-2 py-1.5 text-sm"
              placeholder="e.g. Personal"
              value={newParentLabel}
              onChange={(e) => setNewParentLabel(e.target.value)}
              disabled={!canWrite || pending}
            />
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={newParentExclude}
                onChange={(e) => setNewParentExclude(e.target.checked)}
                disabled={!canWrite || pending}
              />
              Exclude from accounting
            </label>
            <Button
              type="button"
              size="sm"
              disabled={!canWrite || pending || !newParentLabel.trim()}
              onClick={addParentCategory}
            >
              Add category
            </Button>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Add subcategory</h3>
            <select
              className="rounded border bg-background px-2 py-1.5 text-sm"
              value={parentId}
              onChange={(e) => setParentId(e.target.value)}
              disabled={!canWrite || pending}
            >
              <option value="">Parent category…</option>
              {parents.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <input
              className="rounded border bg-background px-2 py-1.5 text-sm"
              placeholder="New subcategory label"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              disabled={!canWrite || pending}
            />
            <Button
              type="button"
              size="sm"
              disabled={!canWrite || pending || !parentId || !newLabel.trim()}
              onClick={addSubcategory}
            >
              Add subcategory
            </Button>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Taxonomy ({taxonomy.length} nodes)</h3>
            <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
              {taxonomy.map((t) => {
                const parent = t.parent_id
                  ? taxonomy.find((p) => p.id === t.parent_id)
                  : undefined;
                const eff = effectiveExclude(
                  {
                    id: t.id,
                    parent_id: t.parent_id,
                    exclude_from_accounting: t.exclude_from_accounting ?? null,
                  },
                  parent
                    ? {
                        id: parent.id,
                        parent_id: parent.parent_id,
                        exclude_from_accounting: parent.exclude_from_accounting ?? null,
                      }
                    : null,
                );
                const raw = t.exclude_from_accounting;
                return (
                  <li key={t.id} className="rounded border p-2">
                    <div className={t.parent_id ? "pl-2 text-muted-foreground" : "font-medium"}>
                      {t.label}
                      <span className="ml-1 text-[10px] text-muted-foreground">
                        (eff: {eff ? "exclude" : "include"})
                      </span>
                    </div>
                    {canWrite ? (
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        <select
                          className="rounded border bg-background px-1 py-0.5 text-[11px]"
                          value={raw === true ? "yes" : raw === false ? "no" : "inherit"}
                          disabled={pending}
                          onChange={(e) => {
                            const v = e.target.value;
                            setExclude(
                              t.id,
                              v === "yes" ? true : v === "no" ? false : null,
                            );
                          }}
                        >
                          <option value="inherit">Inherit</option>
                          <option value="yes">Exclude</option>
                          <option value="no">Include</option>
                        </select>
                        {t.parent_id ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-6 px-1 text-xs"
                            disabled={pending}
                            onClick={() => softDisable(t.id)}
                          >
                            Disable
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium">
                Rules ({visibleRules.length === ruleCount ? ruleCount : `${visibleRules.length} of ${ruleCount}`})
              </h3>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <input type="checkbox" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} />
                Show disabled
              </label>
            </div>
            <input
              type="search"
              aria-label="Search rules"
              className="rounded border bg-background px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Search pattern, category, rule id, last-4…"
              value={ruleQuery}
              onChange={(e) => setRuleQuery(e.target.value)}
            />
            <ul className="space-y-2 text-xs">
              {visibleRules.map((r) => {
                const off = r.enabled === false;
                const dup = dupOf.get(r.id);
                const dr = dryRuns[r.id];
                const expanded = openRule === r.id;
                return (
                  <li key={r.id} className={cn("rounded-md border p-2", off && "bg-muted/40")}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[11px] text-muted-foreground">#{r.priority}</span>
                      <span className="font-mono text-[11px] break-all">{r.id}</span>
                      {off ? <Badge variant="outline">Off</Badge> : null}
                      {dup ? <Badge variant="destructive">Duplicate of {dup}</Badge> : null}
                    </div>
                    <div className="mt-1 text-sm font-medium text-foreground">
                      {r.category_label ?? r.category_id}
                      <span className="text-muted-foreground">
                        {" › "}
                        {r.subcategory_id ? (r.subcategory_label ?? r.subcategory_id) : "no subcategory"}
                      </span>
                    </div>
                    <div className="text-muted-foreground break-words">{ruleCriteria(r)}</div>
                    <div className="text-muted-foreground">
                      Applied to {r.applied_count.toLocaleString("en-US")} transaction
                      {r.applied_count === 1 ? "" : "s"}
                    </div>
                    {expanded ? (
                      <div className="mt-2 flex flex-col gap-1 rounded bg-muted/40 p-2">
                        {r.notes ? <p>{r.notes}</p> : null}
                        {r.updated_at ? (
                          <p className="text-muted-foreground">Last changed {r.updated_at.slice(0, 16)} UTC</p>
                        ) : null}
                        {dr ? (
                          <>
                            <p>
                              Dry-run: matches <span className="font-medium">{dr.matches}</span> · would apply to{" "}
                              <span className="font-medium">{dr.would_apply}</span> (now {dr.currently_applied}) ·{" "}
                              {dr.shadowed} taken by a higher-priority rule · {dr.overrides} override(s) kept
                            </p>
                            {dr.sample.length ? (
                              <ul className="mt-1 space-y-0.5">
                                {dr.sample.map((m, i) => (
                                  <li key={i} className="flex gap-2">
                                    <span className="tabular-nums text-muted-foreground">{m.date}</span>
                                    <span className="flex-1 truncate">{m.name}</span>
                                    <span className="tabular-nums">{formatDollars(m.amount)}</span>
                                    <span className="text-muted-foreground">{m.status}</span>
                                  </li>
                                ))}
                              </ul>
                            ) : null}
                          </>
                        ) : (
                          <p className="text-muted-foreground">Dry-run shows what this rule matches today.</p>
                        )}
                      </div>
                    ) : null}
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        aria-expanded={expanded}
                        onClick={() => setOpenRule(expanded ? null : r.id)}
                      >
                        {expanded ? "Hide" : "View"}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        disabled={pending}
                        onClick={() => dryRun(r.id)}
                      >
                        Dry-run
                      </Button>
                      {canWrite ? (
                        <>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-7 px-2 text-xs"
                            disabled={pending}
                            onClick={() => toggleRule(r.id, off)}
                          >
                            {off ? "Enable" : "Disable"}
                          </Button>
                          {r.applied_count > 0 || !off ? (
                            confirmRevert === r.id ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="destructive"
                                className="h-7 px-2 text-xs"
                                disabled={pending || jobLive}
                                onClick={() => revert(r.id)}
                              >
                                Confirm revert ({r.applied_count})
                              </Button>
                            ) : (
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                className="h-7 px-2 text-xs"
                                disabled={pending || jobLive}
                                title={jobLive ? "Waiting for the running Reapply to finish" : "Disable and re-categorize its transactions"}
                                onClick={() => setConfirmRevert(r.id)}
                              >
                                Revert
                              </Button>
                            )
                          ) : null}
                        </>
                      ) : null}
                    </div>
                  </li>
                );
              })}
              {!visibleRules.length ? (
                <li className="text-muted-foreground">No rules match “{ruleQuery}”.</li>
              ) : null}
            </ul>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
