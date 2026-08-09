"use client";

import { useState } from "react";
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
  setCategoryRuleEnabledAction,
  setTaxonomyExcludeAction,
  setTaxonomyNodeEnabledAction,
  upsertTaxonomyNodeAction,
} from "@/app/accounting/actions";
import type { TaxonomyOption } from "@/components/accounting/AccountingLedger";
import { effectiveExclude } from "@/lib/plaid/exclude-accounting";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { okAck } from "@/lib/actions/types";

export interface RuleListItem {
  id: string;
  priority: number;
  match_operator: string;
  match_pattern: string;
  amount_sign: string | null;
  from_mask?: string | null;
  to_mask?: string | null;
  enabled: boolean | null;
}

export function AccountingRulesDrawer({
  canWrite,
  ruleCount,
  taxonomy,
  rules,
}: {
  canWrite: boolean;
  ruleCount: number;
  taxonomy: TaxonomyOption[];
  rules: RuleListItem[];
}) {
  const [open, setOpen] = useState(false);
  const { isPending: pending, stage, error, run } = useConsoleAction();
  const [msg, setMsg] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [parentId, setParentId] = useState("");
  const [newParentLabel, setNewParentLabel] = useState("");
  const [newParentExclude, setNewParentExclude] = useState(false);

  const parents = taxonomy.filter((t) => !t.parent_id);

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
    void run(async () => {
      const ack = await dryRunRuleAction(id);
      if (!ack.ok) return ack;
      const n = ack.data!;
      setMsg(`Dry-run ${id}: ${n} matching txns`);
      return okAck({ message: `Dry-run ${id}: ${n} matching txns` });
    });
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
            <h3 className="text-sm font-medium">Rules ({ruleCount})</h3>
            <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
              {rules.slice(0, 50).map((r) => (
                <li key={r.id} className="rounded border p-2">
                  <div className="font-mono">
                    #{r.priority} {r.id}
                    {r.enabled === false ? " (off)" : ""}
                  </div>
                  <div className="text-muted-foreground">
                    {r.match_pattern
                      ? `${r.match_operator} "${r.match_pattern}"`
                      : "(no name pattern)"}{" "}
                    ({r.amount_sign || "any"})
                    {r.from_mask ? ` · from …${r.from_mask}` : ""}
                    {r.to_mask ? ` · to …${r.to_mask}` : ""}
                  </div>
                  <div className="mt-1 flex gap-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-6 text-xs"
                      disabled={pending}
                      onClick={() => dryRun(r.id)}
                    >
                      Dry-run
                    </Button>
                    {canWrite ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-6 text-xs"
                        disabled={pending}
                        onClick={() => toggleRule(r.id, r.enabled === false)}
                      >
                        {r.enabled === false ? "Enable" : "Disable"}
                      </Button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
