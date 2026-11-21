import { Camera, Check, ChevronLeft, ChevronRight, ClipboardList, CloudOff, FileText, ListChecks, Lock, MessageSquare, MousePointerClick, Plus, Settings, UserPlus, Users, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, isOffline, type Bootstrap, type Day, type Session, type Staff, type Task } from "./api";
import { CountForm } from "./Count";
import { PersonPicker } from "./People";
import { applyPending, flush, loadQueue, push, type Pending } from "./queue";
import { TaskDetail } from "./TaskDetail";
import { addDays, Avatar, Button, cx, dayLabel, Field, firstName, Input, inputClass, Notice, Sheet, time, useWide } from "./ui";

const REFRESH_MS = 60 * 1000;

const FILTERS = ["all", "mine", "unassigned"] as const;
type Filter = (typeof FILTERS)[number];

const cacheKey = (date: string) => `st.day.${date}`;
const readCache = (date: string): Day | null => JSON.parse(localStorage.getItem(cacheKey(date)) ?? "null");

/** The shift people are most likely working on now: the first one not yet due, else the last. */
function currentShift(day: Day): string {
  const now = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago" });
  return (day.shifts.find((s) => now <= s.due) ?? day.shifts[day.shifts.length - 1]).id;
}

interface Props {
  boot: Bootstrap;
  session: Session;
  /** Null on a person's own Google account: there is nobody to hand the screen to. */
  onLock: (() => void) | null;
  onManage: () => void;
}

/** Who to ask for when assigning: one task, or every task in `ids`. */
interface Picking {
  title: string;
  ids: string[];
  current?: string | null;
}

export function DayScreen({ boot, session, onLock, onManage }: Props) {
  const [date, setDate] = useState(boot.today);
  const [server, setServer] = useState<Day | null>(() => readCache(boot.today));
  const [queue, setQueue] = useState<Pending[]>(loadQueue);
  const [shift, setShift] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [hideDone, setHideDone] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [picking, setPicking] = useState<Picking | null>(null);
  const [adding, setAdding] = useState(false);
  const [counting, setCounting] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const wide = useWide();
  const isManager = session.staff.role === "manager";
  const me = session.staff.id;
  const names = useMemo(() => new Map(boot.staff.map((s) => [s.id, s.name])), [boot.staff]);

  const refresh = useCallback(async () => {
    try {
      const sent = await flush();
      setQueue(loadQueue());
      if (sent.rejected.length) setError(sent.rejected[0]);
      const fresh = await api.day(session.token, date);
      localStorage.setItem(cacheKey(date), JSON.stringify(fresh));
      setServer(fresh);
      setOffline(false);
    } catch (e) {
      if (isOffline(e)) setOffline(true);
      else setError((e as Error).message);
    }
  }, [session.token, date]);

  useEffect(() => {
    setServer(readCache(date));
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    const wake = () => document.visibilityState === "visible" && refresh();
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", wake);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [date, refresh]);

  const day = useMemo(() => (server && server.date === date ? applyPending(server, queue) : null), [server, queue, date]);
  const activeShift = shift ?? (day ? currentShift(day) : null);
  const tasks = useMemo(() => (day ? day.tasks.filter((t) => t.shift === activeShift) : []), [day, activeShift]);
  const scheduled = useMemo(() => new Set(Object.values(day?.teams ?? {}).flatMap((team) => team.map((m) => m.id ?? ""))), [day]);
  const openTask = day?.tasks.find((t) => t.id === open) ?? null;

  const change = (...changes: Omit<Pending, "at" | "token" | "staff_id">[]) => {
    const at = new Date().toISOString();
    setError("");
    setQueue(push(...changes.map((c) => ({ ...c, at, token: session.token, staff_id: me }))));
    refresh();
  };

  const toggle = (t: Task) => {
    if (t.source === "clickup") return;
    if (t.status === "open" && t.needs_photo && !t.photos?.length && !isManager) {
      setOpen(t.id);
      return;
    }
    if (t.status === "done" && !isManager && t.completed_by !== me) {
      setError(`${firstName(names.get(t.completed_by ?? "")) || "Someone else"} ticked this. Ask them or a manager to undo it.`);
      return;
    }
    change({ id: t.id, kind: t.status === "open" ? "complete" : "reopen" });
  };

  const assign = (ids: string[], assignee_id: string | null) => change(...ids.map((id) => ({ id, kind: "assign" as const, assignee_id })));
  const replaceTask = (t: Task) => setServer((d) => (d ? { ...d, tasks: d.tasks.map((x) => (x.id === t.id ? t : x)) } : d));
  const pickFor = (t: Task) => setPicking({ title: "Who is doing this?", ids: [t.id], current: t.assignee_id });

  if (!day) {
    return (
      <div className="flex min-h-full items-center justify-center p-6 text-muted-foreground">
        {offline ? "No connection, and this day is not saved on the device yet." : "Loading…"}
      </div>
    );
  }

  const readOnly = !day.generated;
  const childrenOf = (id: string) => tasks.filter((t) => t.parent_id === id);
  const matches = (t: Task) =>
    (filter === "all" || (filter === "mine" ? t.assignee_id === me : !t.assignee_id)) && (!hideDone || t.status === "open");
  const shows = (t: Task) => matches(t) || childrenOf(t.id).some(matches);
  const top = tasks.filter((t) => !t.parent_id || !tasks.some((p) => p.id === t.parent_id));
  const unassigned = tasks.filter((t) => !t.assignee_id && t.status === "open");
  const counts: Record<Filter, number> = { all: tasks.length, mine: tasks.filter((t) => t.assignee_id === me).length, unassigned: tasks.filter((t) => !t.assignee_id).length };
  const labels: Record<Filter, string> = { all: "All", mine: "Mine", unassigned: "Unassigned" };
  const shiftInfo = day.shifts.find((s) => s.id === activeShift);
  const left = tasks.filter((t) => t.status === "open").length;
  const overdue = left > 0 && date === day.today && Boolean(shiftInfo?.due_at) && new Date() > new Date(shiftInfo!.due_at!);
  const isLastShift = activeShift === day.shifts.at(-1)?.id;
  const showCount = isLastShift && boot.store.count_items.length > 0 && (!readOnly || day.count_submitted);

  const row = (t: Task, sub = false) => (
    <TaskRow
      key={t.id}
      task={t}
      sub={sub}
      steps={childrenOf(t.id)}
      names={names}
      selected={t.id === open}
      readOnly={readOnly}
      onToggle={toggle}
      onOpen={setOpen}
      onAssign={pickFor}
    />
  );

  const detail = openTask && (
    <TaskDetail
      task={openTask}
      subtasks={day.tasks.filter((t) => t.parent_id === openTask.id)}
      shift={day.shifts.find((s) => s.id === openTask.shift)}
      names={names}
      session={session}
      readOnly={readOnly}
      onToggle={toggle}
      onAssign={pickFor}
      onAssignMe={(t) => assign([t.id], me)}
      onChanged={replaceTask}
      onOpen={setOpen}
      onRemoved={() => {
        setOpen(null);
        refresh();
      }}
    />
  );

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col">
      <header className="space-y-3 border-b border-border px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-6">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1">
            <button aria-label="Previous day" onClick={() => setDate(addDays(date, -1))} className="rounded-full p-2 text-muted-foreground hover:bg-muted">
              <ChevronLeft className="size-5" />
            </button>
            <div className="min-w-0 px-1">
              <h1 className="truncate text-xl font-semibold leading-tight">{date === day.today ? "Today" : dayLabel(date)}</h1>
              <p className="truncate text-sm text-muted-foreground">
                {boot.store.name}
                {date === day.today ? (
                  ` · ${dayLabel(date)}`
                ) : (
                  <>
                    {" · "}
                    <button onClick={() => setDate(boot.today)} className="font-medium text-foreground underline underline-offset-2">
                      Back to today
                    </button>
                  </>
                )}
              </p>
            </div>
            <button
              aria-label="Next day"
              disabled={date >= addDays(day.today, 7)}
              onClick={() => setDate(addDays(date, 1))}
              className="rounded-full p-2 text-muted-foreground hover:bg-muted disabled:opacity-30"
            >
              <ChevronRight className="size-5" />
            </button>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {(offline || queue.length > 0) && (
              <span className="flex items-center gap-1.5 rounded-full bg-muted px-3 py-1.5 text-sm text-muted-foreground">
                <CloudOff className="size-4" />
                {queue.length ? `${queue.length} to send` : "Offline"}
              </span>
            )}
            {isManager && (
              <button aria-label="Manage" onClick={onManage} className="rounded-full p-2.5 text-muted-foreground hover:bg-muted">
                <Settings className="size-5" />
              </button>
            )}
            <button
              disabled={!onLock}
              onClick={onLock ?? undefined}
              aria-label={onLock ? `Lock (signed in as ${session.staff.name})` : session.staff.name}
              className="flex items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-3 text-[15px] font-medium enabled:hover:bg-muted"
            >
              <Avatar id={me} name={session.staff.name} />
              <span className="hidden sm:inline">{firstName(session.staff.name)}</span>
              {onLock && <Lock className="size-4 text-muted-foreground" />}
            </button>
          </div>
        </div>

        <div role="tablist" className="grid gap-2" style={{ gridTemplateColumns: `repeat(${day.shifts.length}, minmax(0, 1fr))` }}>
          {day.shifts.map((s) => {
            const mine = day.tasks.filter((t) => t.shift === s.id);
            const done = mine.filter((t) => t.status === "done").length;
            const selected = s.id === activeShift;
            const complete = mine.length > 0 && done === mine.length;
            return (
              <button
                key={s.id}
                role="tab"
                aria-selected={selected}
                onClick={() => {
                  setShift(s.id);
                  setOpen(null);
                }}
                className={cx(
                  "min-w-0 rounded-xl border px-2.5 py-2 text-left transition sm:px-3",
                  selected ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:bg-muted",
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-medium sm:text-[15px]">{s.label}</span>
                  {complete && <Check className={cx("size-4 shrink-0", !selected && "text-success")} strokeWidth={3} />}
                </span>
                <span className={cx("block truncate text-sm tabular-nums", selected ? "text-primary-foreground/70" : "text-muted-foreground")}>
                  {done}/{mine.length}
                  <span className="hidden sm:inline"> · by {time(s.due_at)}</span>
                </span>
                <span className={cx("mt-1.5 block h-1 overflow-hidden rounded-full", selected ? "bg-primary-foreground/20" : "bg-muted")}>
                  <span
                    className={cx("block h-full rounded-full transition-all", complete ? "bg-success" : selected ? "bg-primary-foreground" : "bg-foreground")}
                    style={{ width: `${mine.length ? (100 * done) / mine.length : 0}%` }}
                  />
                </span>
              </button>
            );
          })}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-y-auto px-4 pb-10 pt-3 sm:px-6">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div role="group" aria-label="Show" className="flex gap-1 rounded-lg bg-muted p-1">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  aria-pressed={filter === f}
                  onClick={() => setFilter(f)}
                  className={cx("min-h-9 rounded-md px-3 text-sm font-medium", filter === f ? "bg-card shadow-sm" : "text-muted-foreground hover:text-foreground")}
                >
                  {labels[f]} <span className="tabular-nums opacity-60">{counts[f]}</span>
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3">
              <label className="flex min-h-9 items-center gap-2 text-sm text-muted-foreground">
                <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} className="size-4 accent-foreground" />
                Hide done
              </label>
              {!readOnly && unassigned.length > 0 && (
                <Button
                  variant="outline"
                  onClick={() => setPicking({ title: `Who is doing the ${unassigned.length} unassigned ${shiftInfo?.label ?? ""} tasks?`, ids: unassigned.map((t) => t.id) })}
                  className="min-h-9 px-3 text-sm"
                >
                  <Users className="size-4" />
                  Assign {unassigned.length} unassigned
                </Button>
              )}
            </div>
          </div>

          <p className={cx("mb-2 text-sm", overdue ? "font-medium text-destructive" : "text-muted-foreground")}>
            {tasks.length === 0 ? "" : left === 0 ? "All done for this shift." : `${left} left · ${overdue ? "was due" : "due"} ${time(shiftInfo?.due_at)}`}
          </p>
          {readOnly && (
            <div className="mb-2">
              <Notice>This day was done in ClickUp. It is shown here for reference.</Notice>
            </div>
          )}
          {error && (
            <div className="mb-2 flex items-start gap-2">
              <div className="flex-1">
                <Notice tone="error">{error}</Notice>
              </div>
              <button aria-label="Dismiss" onClick={() => setError("")} className="rounded-full p-2 text-muted-foreground hover:bg-muted">
                <X className="size-4" />
              </button>
            </div>
          )}

          {showCount && (
            <button
              onClick={() => setCounting(true)}
              className="mb-3 flex min-h-14 w-full items-center gap-3 rounded-xl border border-border bg-card px-4 py-2 text-left hover:bg-muted"
            >
              <ClipboardList className={cx("size-5 shrink-0", day.count_submitted ? "text-success" : "text-muted-foreground")} />
              <span className="flex-1">
                <span className="block font-medium">Closing count</span>
                <span className="block text-sm text-muted-foreground">
                  {readOnly ? "Sent. Open to see counts, notes and photos." : day.count_submitted ? "Sent. Open to view or correct it." : "Not sent yet. Counts, notes and photos."}
                </span>
              </span>
              <ChevronRight className="size-5 text-muted-foreground" />
            </button>
          )}

          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
            {top.filter(shows).map((t) => (
              <li key={t.id}>
                {row(t)}
                {childrenOf(t.id).filter(matches).length > 0 && (
                  <ul className="divide-y divide-border border-t border-border bg-muted/40">
                    {childrenOf(t.id)
                      .filter(matches)
                      .map((c) => (
                        <li key={c.id}>{row(c, true)}</li>
                      ))}
                  </ul>
                )}
              </li>
            ))}
            {top.filter(shows).length === 0 && (
              <li className="px-4 py-12 text-center text-muted-foreground">
                {tasks.length === 0
                  ? "Nothing on this list."
                  : filter === "mine"
                    ? "Nothing here is yours yet. Tap the person icon on a task to take it."
                    : hideDone && left === 0
                      ? "All done for this shift."
                      : "Nothing to show."}
              </li>
            )}
            {!readOnly && (
              <li>
                <button onClick={() => setAdding(true)} className="flex min-h-13 w-full items-center gap-3 px-4 text-left text-muted-foreground hover:bg-muted">
                  <Plus className="size-5" />
                  Add a task for this day
                </button>
              </li>
            )}
          </ul>
        </main>

        {wide && (
          <aside className="w-[26rem] shrink-0 overflow-y-auto border-l border-border bg-card xl:w-[30rem]">
            {openTask ? (
              <div className="p-6">
                <div className="mb-4 flex items-start justify-between gap-3">
                  <h2 className="text-xl font-semibold leading-snug">{openTask.title}</h2>
                  <button aria-label="Close" onClick={() => setOpen(null)} className="-m-2 rounded-full p-2 text-muted-foreground hover:bg-muted">
                    <X className="size-5" />
                  </button>
                </div>
                {detail}
              </div>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-10 text-center text-muted-foreground">
                <MousePointerClick className="size-8" />
                <p>Pick a task to see how to do it, who is doing it, photos and notes.</p>
              </div>
            )}
          </aside>
        )}
      </div>

      {!wide && openTask && (
        <Sheet title={openTask.title} onClose={() => setOpen(null)}>
          {detail}
        </Sheet>
      )}
      {picking && (
        <PersonPicker
          title={picking.title}
          staff={boot.staff}
          scheduled={scheduled}
          me={me}
          current={picking.current}
          onClose={() => setPicking(null)}
          onPick={(id) => {
            assign(picking.ids, id);
            setPicking(null);
          }}
        />
      )}
      {adding && activeShift && (
        <AddTask
          shifts={day.shifts}
          shift={activeShift}
          staff={boot.staff}
          onClose={() => setAdding(false)}
          onAdd={async (body) => {
            const t = await api.addTask(session.token, { ...body, date });
            setAdding(false);
            setShift(t.shift);
            await refresh();
            setOpen(t.id);
          }}
        />
      )}
      {counting && (
        <CountForm
          boot={boot}
          session={session}
          date={date}
          names={names}
          readOnly={readOnly}
          onClose={() => {
            setCounting(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

interface RowProps {
  task: Task;
  sub: boolean;
  steps: Task[];
  names: Map<string, string>;
  selected: boolean;
  readOnly: boolean;
  onToggle: (t: Task) => void;
  onOpen: (id: string) => void;
  onAssign: (t: Task) => void;
}

function TaskRow({ task, sub, steps, names, selected, readOnly, onToggle, onOpen, onAssign }: RowProps) {
  const done = task.status === "done";
  const assignee = task.assignee_id ? names.get(task.assignee_id) : undefined;
  const photos = task.photos?.length ?? 0;
  const notes = task.comments?.length ?? 0;
  const meta = "flex items-center gap-1";
  return (
    <div className={cx("flex items-stretch", sub && "pl-6 sm:pl-8", selected && "bg-muted")}>
      <button
        aria-label={done ? `Undo ${task.title}` : `Done: ${task.title}`}
        aria-pressed={done}
        disabled={readOnly}
        onClick={() => onToggle(task)}
        className="flex w-14 shrink-0 items-center justify-center hover:bg-muted disabled:opacity-60 sm:w-16"
      >
        <span className={cx("flex size-7 items-center justify-center rounded-full border-2 transition", done ? "border-success bg-success text-white" : "border-ring", task.pending && "opacity-60")}>
          {done && <Check className="size-4" strokeWidth={3} />}
        </span>
      </button>
      <button onClick={() => onOpen(task.id)} className="flex min-h-15 min-w-0 flex-1 flex-col justify-center gap-0.5 py-2.5 pr-2 text-left hover:bg-muted">
        <span className={cx("text-[16px] leading-snug", done && "text-muted-foreground line-through")}>{task.title}</span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[13px] text-muted-foreground empty:hidden">
          {done && (
            <span className={cx(meta, "text-success")}>
              {[firstName(names.get(task.completed_by ?? "")), time(task.completed_at)].filter(Boolean).join(" · ")}
              {task.pending && " · not sent yet"}
            </span>
          )}
          {steps.length > 0 && (
            <span className={meta}>
              <ListChecks className="size-3.5" />
              {steps.filter((s) => s.status === "done").length}/{steps.length}
            </span>
          )}
          {task.instructions && (
            <span className={meta}>
              <FileText className="size-3.5" />
              How-to
            </span>
          )}
          {(photos > 0 || task.needs_photo) && (
            <span className={cx(meta, photos === 0 && !done && "font-medium text-foreground")}>
              <Camera className="size-3.5" />
              {photos || "Photo needed"}
            </span>
          )}
          {notes > 0 && (
            <span className={meta}>
              <MessageSquare className="size-3.5" />
              {notes}
            </span>
          )}
        </span>
      </button>
      <button
        disabled={readOnly}
        onClick={() => onAssign(task)}
        aria-label={assignee ? `Doing it: ${assignee}. Change` : "Pick who is doing it"}
        className="flex shrink-0 items-center gap-2 px-3 hover:bg-muted disabled:hover:bg-transparent sm:px-4"
      >
        {task.assignee_id && assignee ? (
          <>
            <span className="hidden max-w-28 truncate text-sm text-muted-foreground md:inline">{firstName(assignee)}</span>
            <Avatar id={task.assignee_id} name={assignee} />
          </>
        ) : (
          <span className={cx("flex size-8 items-center justify-center rounded-full border border-dashed border-ring text-muted-foreground", readOnly && "opacity-0")}>
            <UserPlus className="size-4" />
          </span>
        )}
      </button>
    </div>
  );
}

interface AddTaskProps {
  shifts: Day["shifts"];
  shift: string;
  staff: Staff[];
  onClose: () => void;
  onAdd: (body: { title: string; shift: string; instructions: string; assignee_id: string | null }) => Promise<void>;
}

function AddTask({ shifts, shift, staff, onClose, onAdd }: AddTaskProps) {
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [shiftId, setShiftId] = useState(shift);
  const [assignee, setAssignee] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onAdd({ title: title.trim(), shift: shiftId, instructions: instructions.trim(), assignee_id: assignee || null });
    } catch (err) {
      setError(isOffline(err) ? "No connection. Try again when Wi-Fi is back." : (err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Sheet title="Add a task for this day" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="What needs doing">
          <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Shift">
            <select value={shiftId} onChange={(e) => setShiftId(e.target.value)} className={inputClass}>
              {shifts.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Who is doing it">
            <select value={assignee} onChange={(e) => setAssignee(e.target.value)} className={inputClass}>
              <option value="">No one yet</option>
              {staff.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="How to do it (optional)">
          <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3} className={cx(inputClass, "py-2")} />
        </Field>
        <p className="text-sm text-muted-foreground">Photos and notes can be added once the task is on the list.</p>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" disabled={!title.trim() || busy} className="w-full">
          Add task
        </Button>
      </form>
    </Sheet>
  );
}
