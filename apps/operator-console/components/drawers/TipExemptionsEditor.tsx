"use client";

import { useMemo, useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { applyTipExemptionsAction } from "@/app/payroll/actions";
import type { TipExemptionDraft } from "@/lib/bq/writes";
import type { AdpShiftRow, TipExemptionRow } from "@/lib/bq/queries";

type DraftKey = string; // `${date}|${employee}`

type RowDraft = {
  exempted: boolean;
  entireShift: boolean;
  start: string;
  end: string;
  note: string;
  dirty: boolean;
};

function keyOf(date: string, employee: string): DraftKey {
  return `${date}|${employee}`;
}

function seedFromExemption(ex: TipExemptionRow | undefined): RowDraft {
  if (!ex) {
    return { exempted: false, entireShift: true, start: "", end: "", note: "", dirty: false };
  }
  const hasWindow = Boolean(ex.exempt_start && ex.exempt_end);
  return {
    exempted: true,
    entireShift: !hasWindow,
    start: ex.exempt_start ?? "",
    end: ex.exempt_end ?? "",
    note: ex.note ?? "",
    dirty: false,
  };
}

function draftToAction(
  employeeName: string,
  date: string,
  d: RowDraft,
): TipExemptionDraft | null {
  if (!d.dirty) return null;
  if (!d.exempted) {
    return { employeeName, date, mode: "clear", note: d.note };
  }
  if (d.entireShift) {
    return { employeeName, date, mode: "whole", note: d.note };
  }
  return {
    employeeName,
    date,
    mode: "window",
    exemptStart: d.start,
    exemptEnd: d.end,
    note: d.note,
  };
}

/**
 * Tip Exemptions editor (Issue #167).
 * Local draft state — nothing writes until Update (RestockImportDrawer pattern).
 * Editable only when `editable` (open current pay period).
 */
export function TipExemptionsEditor({
  shifts,
  exemptions,
  employees,
  editable,
  periodLabel,
}: {
  shifts: AdpShiftRow[];
  exemptions: TipExemptionRow[];
  employees: string[];
  editable: boolean;
  periodLabel: string;
}) {
  const exemptionByKey = useMemo(() => {
    const m = new Map<DraftKey, TipExemptionRow>();
    for (const e of exemptions) m.set(keyOf(e.date, e.employee_name), e);
    return m;
  }, [exemptions]);

  const [drafts, setDrafts] = useState<Record<DraftKey, RowDraft>>(() => {
    const init: Record<DraftKey, RowDraft> = {};
    for (const s of shifts) {
      const k = keyOf(s.date, s.employee_name);
      init[k] = seedFromExemption(exemptionByKey.get(k));
    }
    for (const e of exemptions) {
      const k = keyOf(e.date, e.employee_name);
      if (!init[k]) init[k] = seedFromExemption(e);
    }
    return init;
  });

  const [orphanEmp, setOrphanEmp] = useState(employees[0] ?? "");
  const [orphanDate, setOrphanDate] = useState("");
  const [orphanStart, setOrphanStart] = useState("");
  const [orphanEnd, setOrphanEnd] = useState("");
  const [orphanNote, setOrphanNote] = useState("Meeting");
  const [status, setStatus] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function patch(k: DraftKey, partial: Partial<RowDraft>) {
    setDrafts((prev) => ({
      ...prev,
      [k]: { ...(prev[k] ?? seedFromExemption(undefined)), ...partial, dirty: true },
    }));
  }

  function addOrphan() {
    if (!editable) return;
    if (!orphanEmp || !orphanDate || !orphanStart || !orphanEnd) {
      setStatus("Orphan exemption needs employee, date, start, and end.");
      return;
    }
    const k = keyOf(orphanDate, orphanEmp);
    setDrafts((prev) => ({
      ...prev,
      [k]: {
        exempted: true,
        entireShift: false,
        start: orphanStart,
        end: orphanEnd,
        note: orphanNote,
        dirty: true,
      },
    }));
    setStatus(`Queued orphan exemption for ${orphanEmp} on ${orphanDate}.`);
  }

  function handleUpdate() {
    const actions: TipExemptionDraft[] = [];
    for (const [k, d] of Object.entries(drafts)) {
      const [date, employeeName] = k.split("|");
      const a = draftToAction(employeeName, date, d);
      if (a) actions.push(a);
    }
    if (!actions.length) {
      setStatus("No changes to apply.");
      return;
    }
    startTransition(async () => {
      try {
        const res = await applyTipExemptionsAction(actions);
        setStatus(
          `Updated ${actions.length} exemption(s); recomputing ${res.recomputed.join(", ") || "—"}.`,
        );
        setDrafts((prev) => {
          const next = { ...prev };
          for (const k of Object.keys(next)) {
            next[k] = { ...next[k], dirty: false };
          }
          return next;
        });
      } catch (e) {
        setStatus(`Update failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  const orphanRows = exemptions.filter((e) => !e.has_shift);
  const dirtyCount = Object.values(drafts).filter((d) => d.dirty).length;

  return (
    <div className="flex flex-col gap-4">
      {!editable ? (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Historical — view only. Tip exemptions are editable only for the current open pay period
          ({periodLabel}).
        </p>
      ) : null}

      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Shifts · tip exemptions · {periodLabel}
        </h2>
        {editable ? (
          <Button size="sm" disabled={isPending || dirtyCount === 0} onClick={handleUpdate}>
            {isPending ? "Updating…" : `Update${dirtyCount ? ` (${dirtyCount})` : ""}`}
          </Button>
        ) : null}
      </div>

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
            <tr>
              <th className="p-2">Date</th>
              <th className="p-2">Employee</th>
              <th className="p-2">In</th>
              <th className="p-2">Out</th>
              <th className="p-2">Hours</th>
              <th className="p-2">Exempt?</th>
              <th className="p-2">Entire shift</th>
              <th className="p-2">Window start</th>
              <th className="p-2">Window end</th>
              <th className="p-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            {shifts.map((s) => {
              const k = keyOf(s.date, s.employee_name);
              const d = drafts[k] ?? seedFromExemption(exemptionByKey.get(k));
              return (
                <tr key={k} className="border-t">
                  <td className="p-2 whitespace-nowrap">{s.date}</td>
                  <td className="p-2 whitespace-nowrap">{s.employee_name}</td>
                  <td className="p-2">{s.in_time}</td>
                  <td className="p-2">{s.out_time}</td>
                  <td className="p-2">{Number(s.total_hours).toFixed(2)}</td>
                  <td className="p-2">
                    <input
                      type="checkbox"
                      checked={d.exempted}
                      disabled={!editable}
                      onChange={(e) => patch(k, { exempted: e.target.checked })}
                    />
                  </td>
                  <td className="p-2">
                    <input
                      type="checkbox"
                      checked={d.entireShift}
                      disabled={!editable || !d.exempted}
                      onChange={(e) => patch(k, { entireShift: e.target.checked })}
                    />
                  </td>
                  <td className="p-2">
                    <Input
                      className="h-8 w-24"
                      placeholder="HH:MM"
                      value={d.start}
                      disabled={!editable || !d.exempted || d.entireShift}
                      onChange={(e) => patch(k, { start: e.target.value })}
                    />
                  </td>
                  <td className="p-2">
                    <Input
                      className="h-8 w-24"
                      placeholder="HH:MM"
                      value={d.end}
                      disabled={!editable || !d.exempted || d.entireShift}
                      onChange={(e) => patch(k, { end: e.target.value })}
                    />
                  </td>
                  <td className="p-2">
                    <Input
                      className="h-8 w-40"
                      value={d.note}
                      disabled={!editable}
                      onChange={(e) => patch(k, { note: e.target.value })}
                    />
                  </td>
                </tr>
              );
            })}
            {shifts.length === 0 ? (
              <tr>
                <td colSpan={10} className="p-3 text-muted-foreground">
                  No ADP shifts in this period.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">Exemptions table</h2>
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="p-2">Date</th>
                <th className="p-2">Employee</th>
                <th className="p-2">Mode</th>
                <th className="p-2">Window</th>
                <th className="p-2">Notes</th>
                <th className="p-2">Shift</th>
              </tr>
            </thead>
            <tbody>
              {exemptions.map((e) => {
                const k = keyOf(e.date, e.employee_name);
                const draft = drafts[k];
                const start = draft?.dirty ? draft.start : e.exempt_start;
                const end = draft?.dirty ? draft.end : e.exempt_end;
                const entire = draft?.dirty ? draft.entireShift : !(e.exempt_start && e.exempt_end);
                const mode = entire ? "whole-day" : "window";
                return (
                  <tr
                    key={k}
                    className={`border-t ${!e.has_shift ? "bg-amber-50" : ""}`}
                  >
                    <td className="p-2 whitespace-nowrap">{e.date}</td>
                    <td className="p-2 whitespace-nowrap">{e.employee_name}</td>
                    <td className="p-2">{mode}</td>
                    <td className="p-2">
                      {entire ? "—" : `${start ?? "?"}–${end ?? "?"}`}
                    </td>
                    <td className="p-2">{draft?.dirty ? draft.note : e.note}</td>
                    <td className="p-2">
                      {e.has_shift ? "linked" : "No shift associated"}
                    </td>
                  </tr>
                );
              })}
              {exemptions.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-3 text-muted-foreground">
                    No tip exemptions recorded for this period.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {orphanRows.length ? (
          <p className="text-xs text-muted-foreground">
            Amber rows have no matching ADP shift yet — exemption is stored and will apply when
            the shift lands.
          </p>
        ) : null}
      </div>

      {editable ? (
        <div className="flex flex-wrap items-end gap-2 rounded-md border p-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Orphan employee</label>
            <select
              className="h-9 rounded-md border px-2 text-sm"
              value={orphanEmp}
              onChange={(e) => setOrphanEmp(e.target.value)}
            >
              {employees.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Date</label>
            <Input
              className="w-36"
              type="date"
              value={orphanDate}
              onChange={(e) => setOrphanDate(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Start</label>
            <Input
              className="w-24"
              placeholder="HH:MM"
              value={orphanStart}
              onChange={(e) => setOrphanStart(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">End</label>
            <Input
              className="w-24"
              placeholder="HH:MM"
              value={orphanEnd}
              onChange={(e) => setOrphanEnd(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Note</label>
            <Input
              className="w-40"
              value={orphanNote}
              onChange={(e) => setOrphanNote(e.target.value)}
            />
          </div>
          <Button size="sm" variant="secondary" onClick={addOrphan}>
            Queue orphan exemption
          </Button>
        </div>
      ) : null}

      {status ? <p className="text-xs text-muted-foreground">{status}</p> : null}
    </div>
  );
}
