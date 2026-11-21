import { useEffect, useState } from "react";
import { api, isOffline, type Bootstrap, type Repeat, type Session, type Template } from "./api";
import { EVERY_DAY, RepeatFields, repeatLabel } from "./Repeat";
import { Button, cx, Field, Input, inputClass, Notice, Sheet } from "./ui";

/** What the form starts from: nothing, a task being copied, or a repeating task being edited. */
export type TaskDraft = Partial<Template> & { copy_from?: string; assignee_id?: string | null };

export interface SavedTask {
  /** The task on the day being looked at, when the saved task is on that day's list. */
  taskId: string;
  shift: string;
  message: string;
}

interface Props {
  boot: Bootstrap;
  session: Session;
  /** The day being looked at. */
  date: string;
  title: string;
  initial: TaskDraft;
  /** Start as a one-day task. Ignored when editing a task that already repeats. */
  once: boolean;
  onClose: () => void;
  onSaved: (saved: SavedTask) => void;
}

const repeatOf = (t: TaskDraft): Repeat => ({
  weekdays: t.weekdays ?? [],
  every_weeks: t.every_weeks ?? 1,
  month_days: t.month_days ?? [],
  start_date: t.start_date ?? null,
  end_date: t.end_date ?? null,
});

/**
 * The one place a task is created, copied or changed: for one day, or repeating.
 * Anyone adds a task for the day; a repeating task is a manager's to set up.
 */
export function TaskForm({ boot, session, date, title, initial, once, onClose, onSaved }: Props) {
  const isManager = session.staff.role === "manager";
  const editing = Boolean(initial.id);
  const [t, setT] = useState<TaskDraft>(initial);
  const [repeat, setRepeat] = useState<Repeat | null>(editing || (isManager && !once) ? repeatOf(initial) : null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (patch: TaskDraft) => setT((cur) => ({ ...cur, ...patch }));
  const shift = t.shift ?? boot.store.shifts[0].id;

  useEffect(() => {
    if (!isManager) return;
    api
      .templates(session.token)
      .then((r) => setTemplates(r.templates))
      .catch(() => undefined);
  }, [isManager, session.token]);

  const parents = templates.filter((p) => !p.parent_id && p.shift === shift && p.id !== t.id);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const label = boot.store.shifts.find((s) => s.id === shift)?.label ?? "";
      if (repeat) {
        const order = t.order ?? (templates.filter((x) => x.shift === shift && !x.parent_id).at(-1)?.order ?? 0) + 1;
        const { template } = await api.saveTemplate(session.token, { ...t, ...repeat, shift, order, title: t.title?.trim(), instructions: t.instructions?.trim() });
        onSaved({ taskId: `${boot.store.id}_${date}_${template.id}`, shift, message: `${editing ? "Saved" : `Added to ${label}`} · ${repeatLabel(template, boot.today).toLowerCase()}` });
      } else {
        const task = await api.addTask(session.token, {
          title: (t.title ?? "").trim(),
          shift,
          date,
          instructions: t.instructions?.trim(),
          assignee_id: t.assignee_id || null,
          needs_photo: Boolean(t.needs_photo),
        });
        onSaved({ taskId: task.id, shift, message: `Added to ${label}` });
      }
    } catch (err) {
      setError(isOffline(err) ? "No connection. Try again when Wi-Fi is back." : (err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Sheet title={title} onClose={onClose}>
      <form onSubmit={save} className="space-y-4">
        <Field label="What needs doing">
          <Input autoFocus={!editing} value={t.title ?? ""} onChange={(e) => set({ title: e.target.value })} />
        </Field>
        <Field label="How to do it (optional)">
          <textarea rows={3} value={t.instructions ?? ""} onChange={(e) => set({ instructions: e.target.value })} className={cx(inputClass, "py-2")} placeholder="Steps, amounts, where things are kept…" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Shift">
            <select value={shift} onChange={(e) => set({ shift: e.target.value, parent_id: null })} className={inputClass}>
              {boot.store.shifts.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          {repeat ? (
            <Field label="Step of (optional)">
              <select value={t.parent_id ?? ""} onChange={(e) => set({ parent_id: e.target.value || null })} className={inputClass}>
                <option value="">A task on its own</option>
                {parents.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <Field label="Who is doing it">
              <select value={t.assignee_id ?? ""} onChange={(e) => set({ assignee_id: e.target.value || null })} className={inputClass}>
                <option value="">No one yet</option>
                {boot.staff.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </div>

        {isManager ? (
          <RepeatFields value={repeat} onChange={setRepeat} date={date} allowOnce={!editing} />
        ) : (
          <p className="text-sm text-muted-foreground">This adds the task to this day's list. A manager can make a task repeat.</p>
        )}

        {isManager && (
          <div className="space-y-1">
            <label className="flex min-h-11 items-center gap-3">
              <input type="checkbox" checked={Boolean(t.needs_photo)} onChange={(e) => set({ needs_photo: e.target.checked })} className="size-5 accent-primary" />
              Needs a photo before it can be marked done
            </label>
            {editing && (
              <>
                <label className="flex min-h-11 items-center gap-3">
                  <input type="checkbox" checked={t.active !== false} onChange={(e) => set({ active: e.target.checked })} className="size-5 accent-primary" />
                  On the list (untick to stop it without deleting it)
                </label>
                <Field label="Position in the list">
                  <Input inputMode="decimal" value={t.order ?? ""} onChange={(e) => set({ order: Number(e.target.value) || 0 })} className="max-w-32" />
                </Field>
              </>
            )}
          </div>
        )}
        {repeat && editing && <Notice>The change shows from today on tasks not done yet. Earlier versions are kept.</Notice>}
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" disabled={!t.title?.trim() || busy} className="w-full">
          {busy ? "Saving…" : editing ? "Save" : "Add task"}
        </Button>
      </form>
    </Sheet>
  );
}

export const blankDraft = (shift: string): TaskDraft => ({ shift, active: true, ...EVERY_DAY });
