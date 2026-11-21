import { CalendarClock, Camera, Check, ChevronDown, ChevronLeft, ChevronRight, Clapperboard, ClipboardList, CloudOff, FileText, Images, KeyRound, ListChecks, Lock, MessageSquare, MousePointerClick, Plus, Search, Settings, UserPlus, Users, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isOffline, SHOP, type Bootstrap, type Day, type Session, type Task } from "./api";
import { CountForm } from "./Count";
import { FeedbackButton } from "./Feedback";
import { shrink } from "./Photos";
import { PersonPicker } from "./People";
import { applyPending, flush, loadQueue, push, type Pending } from "./queue";
import { setWhere, track } from "./steps";
import { TaskDetail } from "./TaskDetail";
import { blankDraft, TaskForm, type TaskDraft } from "./TaskForm";
import { addDays, Avatar, Button, cx, dayLabel, firstName, inputClass, Notice, Sheet, shiftTone, Tick, time, Toast, useWide } from "./ui";

const REFRESH_MS = 60 * 1000;
const TOAST_MS = 2800;
const CONFETTI_MS = 1600;
/** Açaí, mango, banana, palm leaf, pitaya, berry. */
const CONFETTI = ["oklch(0.42 0.15 305)", "oklch(0.77 0.16 62)", "oklch(0.9 0.15 98)", "var(--success)", "oklch(0.68 0.2 355)", "var(--primary)"];
const CHEERS = ["Berry well done.", "Açaí-mazing work.", "Bowl-ed it over.", "That was smooth-ie.", "Fresh work, team.", "Peachy keen."];

/** The same list on the same day always gets the same cheer. */
function cheer(key: string) {
  let h = 0;
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) | 0;
  return CHEERS[Math.abs(h) % CHEERS.length];
}

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
  /** Null on a person's own Google account: there is nobody to hand the screen to. On the shop session: manager sign-in. */
  onLock: (() => void) | null;
  onManage: () => void;
}

/** Who to ask for when assigning: one task, or every task in `ids`. */
interface Picking {
  title: string;
  ids: string[];
  current?: string | null;
}

/** The task form, opened to add, copy or change a task. */
interface Forming {
  title: string;
  initial: TaskDraft;
  once: boolean;
}

export function DayScreen({ boot, session, onLock, onManage }: Props) {
  const [date, setDate] = useState(boot.today);
  const [server, setServer] = useState<Day | null>(() => readCache(boot.today));
  const [queue, setQueue] = useState<Pending[]>(loadQueue);
  const [shift, setShift] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [hideDone, setHideDone] = useState(false);
  const [query, setQuery] = useState("");
  const [unfolded, setUnfolded] = useState<Set<string>>(() => new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [picking, setPicking] = useState<Picking | null>(null);
  const [form, setForm] = useState<Forming | null>(null);
  const [toast, setToast] = useState("");
  const [party, setParty] = useState("");
  const [counting, setCounting] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const wide = useWide();
  const isManager = session.staff.role === "manager";
  const me = session.staff.id;
  const shop = me === SHOP;
  const names = useMemo(() => new Map([[SHOP, "Shop"], ...boot.staff.map((s): [string, string] => [s.id, s.name])]), [boot.staff]);

  const refresh = useCallback(async () => {
    try {
      const sent = await flush();
      if (sent.rejected.length) setError(sent.rejected[0]);
      const fresh = await api.day(session.token, date);
      localStorage.setItem(cacheKey(date), JSON.stringify(fresh));
      // Sent changes leave the queue only with the day that includes them, or ticks flicker back.
      setServer(fresh);
      setQueue(loadQueue());
      setOffline(false);
    } catch (e) {
      setQueue(loadQueue());
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
  useEffect(() => {
    setWhere({ screen: "List", date, shift: activeShift, task: openTask?.title, task_id: openTask?.id, source: openTask?.source });
  }, [date, activeShift, openTask?.id, openTask?.title, openTask?.source]);
  // Counted the way ClickUp reports a list: tasks, not the steps inside them.
  const whole = tasks.filter((t) => !t.parent_id);
  const left = whole.filter((t) => t.status === "open").length;

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // Confetti only when the last task of a list is ticked here and now, not when a finished list is opened.
  const listKey = `${date}/${activeShift}`;
  const before = useRef({ key: "", left: 0 });
  useEffect(() => {
    if (before.current.key === listKey && before.current.left > 0 && left === 0 && whole.length > 0) {
      setParty(listKey);
      const timer = window.setTimeout(() => setParty(""), CONFETTI_MS);
      before.current = { key: listKey, left };
      return () => window.clearTimeout(timer);
    }
    before.current = { key: listKey, left };
  }, [listKey, left, whole.length]);
  const cheerBanner = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (party) cheerBanner.current?.scrollIntoView({ block: "nearest" });
  }, [party]);

  const change = (...changes: Omit<Pending, "at" | "token" | "staff_id">[]) => {
    const at = new Date().toISOString();
    setError("");
    setQueue(push(...changes.map((c) => ({ ...c, at, token: session.token, staff_id: me }))));
    refresh();
  };

  // A task that needs a photo goes straight to the camera; the photo, once saved, ticks it.
  const camera = useRef<HTMLInputElement>(null);
  const shooting = useRef<string | null>(null);
  const tookPhoto = async (file: File | undefined) => {
    const id = shooting.current;
    shooting.current = null;
    if (camera.current) camera.current.value = "";
    if (!id || !file) return;
    setToast("Saving the photo…");
    try {
      replaceTask(await api.photo(session.token, id, await shrink(file)));
      navigator.vibrate?.(12);
      change({ id, kind: "complete" });
      setToast("Photo saved and ticked");
    } catch (e) {
      setToast("");
      setError(isOffline(e) ? "No connection, so the photo was not saved. Try again when you are back online." : (e as Error).message);
    }
  };

  const toggle = (t: Task) => {
    if (t.status === "open" && t.needs_photo && !t.photos?.length) {
      setOpen(t.id);
      shooting.current = t.id;
      camera.current?.click();
      return;
    }
    if (t.status === "done" && !isManager && t.completed_by !== me && t.ticked_by !== me) {
      setError(`${firstName(names.get(t.completed_by ?? "")) || "Someone else"} ticked this. Ask them or a manager to undo it.`);
      return;
    }
    if (t.status === "open") navigator.vibrate?.(12);
    track("action", `${t.status === "open" ? "tick" : "undo"} ${t.title}`);
    change({ id: t.id, kind: t.status === "open" ? "complete" : "reopen" });
  };

  const assign = (ids: string[], assignee_id: string | null) => {
    track("action", `assign ${ids.length} task(s) to ${assignee_id ? firstName(names.get(assignee_id)) : "no one"}`);
    change(...ids.map((id) => ({ id, kind: "assign" as const, assignee_id })));
    const who = assignee_id === me ? "you" : firstName(names.get(assignee_id ?? ""));
    setToast(assignee_id ? `${ids.length > 1 ? `${ids.length} tasks` : "Task"} assigned to ${who}` : "No one is on it now");
  };
  const replaceTask = (t: Task) => setServer((d) => (d ? { ...d, tasks: d.tasks.map((x) => (x.id === t.id ? t : x)) } : d));
  const pickFor = (t: Task) => setPicking({ title: "Who is doing this?", ids: [t.id], current: t.assignee_id });

  if (!day) {
    if (offline) return <div className="flex min-h-full items-center justify-center p-6 text-muted-foreground">No connection, and this day is not saved on the device yet.</div>;
    return (
      <div role="status" aria-label="Loading" className="mx-auto max-w-7xl space-y-4 p-4 sm:p-6">
        <div className="skeleton h-10 w-48" />
        <div className="grid grid-cols-3 gap-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-16" />
          ))}
        </div>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton h-14" />
        ))}
      </div>
    );
  }

  // Before cutover ClickUp owns the day: ticks here are a try-out the sync undoes, so past days stay as ClickUp left them.
  const clickupDay = !day.generated;
  const readOnly = clickupDay && date < day.today;
  const childrenOf = (id: string) => tasks.filter((t) => t.parent_id === id);
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = (t: Task) => words.every((w) => t.title.toLowerCase().includes(w));
  const matches = (t: Task) =>
    found(t) && (filter === "all" || (filter === "mine" ? t.assignee_id === me : !t.assignee_id)) && (!hideDone || t.status === "open");
  const shows = (t: Task) => matches(t) || childrenOf(t.id).some(matches);
  // Groups start folded; one opens by itself when only its steps match the search or filter.
  const isOpen = (t: Task) => unfolded.has(t.id) || ((words.length > 0 || !matches(t)) && childrenOf(t.id).some(matches));
  const fold = (t: Task) => {
    const opening = !isOpen(t);
    track("action", `${opening ? "open" : "fold"} group ${t.title}`);
    setUnfolded((u) => {
      const next = new Set(u);
      if (opening) next.add(t.id);
      else next.delete(t.id);
      return next;
    });
  };
  const countFound = words.every((w) => "closing inventory count".includes(w));
  const hasCount = boot.store.count_items.length > 0 && (!readOnly || day.count_submitted);
  const elsewhere = words.length
    ? day.shifts
        .filter((s) => s.id !== activeShift)
        .map((s) => ({ ...s, n: day.tasks.filter((t) => t.shift === s.id && found(t)).length + (hasCount && countFound && s.id === day.shifts.at(-1)?.id ? 1 : 0) }))
        .filter((s) => s.n > 0)
    : [];
  const top = tasks.filter((t) => !t.parent_id || !tasks.some((p) => p.id === t.parent_id));
  const unassigned = tasks.filter((t) => !t.assignee_id && t.status === "open" && !isPlanned(t));
  const hasPlanned = tasks.some(isPlanned);
  const counts: Record<Filter, number> = { all: whole.length, mine: whole.filter((t) => t.assignee_id === me).length, unassigned: whole.filter((t) => !t.assignee_id).length };
  const openUnassigned = unassigned.filter((t) => !t.parent_id).length;
  const labels: Record<Filter, string> = { all: "All", mine: "Mine", unassigned: "Unassigned" };
  const shiftInfo = day.shifts.find((s) => s.id === activeShift);
  const overdue = left > 0 && date === day.today && Boolean(shiftInfo?.due_at) && new Date() > new Date(shiftInfo!.due_at!);
  const isLastShift = activeShift === day.shifts.at(-1)?.id;
  const showCount = isLastShift && hasCount && countFound;

  const row = (t: Task, sub = false) => (
    <TaskRow
      key={t.id}
      task={t}
      sub={sub}
      steps={childrenOf(t.id)}
      expanded={sub ? undefined : isOpen(t)}
      onExpand={() => fold(t)}
      names={names}
      selected={t.id === open}
      readOnly={readOnly || isPlanned(t)}
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
      readOnly={readOnly || isPlanned(openTask)}
      onToggle={toggle}
      onAssign={pickFor}
      onAssignMe={(t) => assign([t.id], me)}
      onChanged={replaceTask}
      onOpen={setOpen}
      onDuplicate={(initial, once) => setForm({ title: "Duplicate task", initial, once })}
      onEditDefinition={(definition) => setForm({ title: "Edit repeating task", initial: definition, once: false })}
      onRemoved={() => {
        setOpen(null);
        refresh();
      }}
    />
  );

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col" data-tone={shiftInfo ? shiftTone(shiftInfo.due).tone : undefined}>
      <header className="space-y-3 border-b border-border bg-card px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-6">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1">
            <span aria-hidden className="brand-mark brand-palm mr-1 hidden h-9 shrink-0 text-primary sm:inline-block" />
            <button aria-label="Previous day" onClick={() => setDate(addDays(date, -1))} className="rounded-full p-2 text-muted-foreground hover:bg-muted">
              <ChevronLeft className="size-5" />
            </button>
            <div className="min-w-0 px-1">
              <h1 className="truncate text-xl font-extrabold leading-tight tracking-tight">{date === day.today ? "Today" : dayLabel(date)}</h1>
              <p className="truncate text-sm text-muted-foreground">
                {boot.store.name}
                {date === day.today ? (
                  ` · ${dayLabel(date)}`
                ) : (
                  <>
                    {" · "}
                    <button onClick={() => setDate(boot.today)} className="font-medium text-primary underline underline-offset-2">
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
            <FeedbackButton session={session} />
            {isManager && (
              <button aria-label="Manage" onClick={onManage} className="rounded-full p-2.5 text-muted-foreground hover:bg-muted">
                <Settings className="size-5" />
              </button>
            )}
            {shop ? (
              <button
                aria-label="Manager"
                onClick={onLock ?? undefined}
                className="flex min-h-10 min-w-10 items-center justify-center gap-2 rounded-full border border-border bg-card px-3 text-[15px] font-medium text-muted-foreground transition hover:bg-muted active:scale-95"
              >
                <KeyRound className="size-4" />
                <span className="hidden sm:inline">Manager</span>
              </button>
            ) : (
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
            )}
          </div>
        </div>

        <div role="tablist" className="grid gap-2" style={{ gridTemplateColumns: `repeat(${day.shifts.length}, minmax(0, 1fr))` }}>
          {day.shifts.map((s) => {
            const mine = day.tasks.filter((t) => t.shift === s.id && !t.parent_id);
            const done = mine.filter((t) => t.status === "done").length;
            const selected = s.id === activeShift;
            const complete = mine.length > 0 && done === mine.length;
            const { tone, Icon } = shiftTone(s.due);
            return (
              <button
                key={s.id}
                role="tab"
                data-tone={tone}
                aria-selected={selected}
                onClick={() => {
                  setShift(s.id);
                  setOpen(null);
                }}
                className={cx(
                  "min-w-0 rounded-xl border px-2.5 py-2 text-left transition duration-200 active:scale-[0.98] sm:px-3",
                  selected ? "border-tone bg-tone-soft shadow-card" : "border-border bg-card hover:border-ring hover:bg-muted/60",
                )}
              >
                <span className="flex items-center gap-1.5">
                  <Icon className={cx("hidden size-4 shrink-0 sm:block", selected ? "text-tone-ink" : "text-tone")} />
                  <span className={cx("min-w-0 flex-1 truncate text-[13px] font-semibold sm:text-[15px]", selected && "text-tone-ink")}>{s.label}</span>
                  {complete && <Check className="pop hidden size-4 shrink-0 text-success sm:block" strokeWidth={3.5} />}
                </span>
                <span className="flex items-center gap-1.5 truncate text-sm tabular-nums text-muted-foreground">
                  <Icon className={cx("size-3.5 shrink-0 sm:hidden", selected ? "text-tone-ink" : "text-tone")} />
                  {done}/{mine.length}
                  <span className="hidden sm:inline"> · by {time(s.due_at)}</span>
                  {complete && <Check className="pop size-3.5 shrink-0 text-success sm:hidden" strokeWidth={3.5} />}
                </span>
                <span className={cx("mt-1.5 block h-1.5 overflow-hidden rounded-full", selected ? "bg-card" : "bg-muted")}>
                  <span
                    className={cx("block h-full rounded-full transition-[width,background-color] duration-500 ease-out", complete ? "bg-success" : "bg-tone")}
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
          <div className="relative mb-2">
            <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onBlur={() => query.trim() && track("action", `search "${query.trim()}"`)}
              placeholder="Find a task"
              aria-label="Find a task"
              enterKeyHint="search"
              className={cx(inputClass, "pl-10 pr-11 [&::-webkit-search-cancel-button]:hidden")}
            />
            {query && (
              <button aria-label="Clear search" onClick={() => setQuery("")} className="absolute right-1 top-1/2 flex size-10 -translate-y-1/2 items-center justify-center rounded-full text-muted-foreground hover:bg-muted">
                <X className="size-4" />
              </button>
            )}
          </div>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div role="group" aria-label="Show" className="flex gap-1 rounded-lg bg-muted p-1">
              {FILTERS.filter((f) => !(shop && f === "mine")).map((f) => (
                <button
                  key={f}
                  aria-pressed={filter === f}
                  onClick={() => setFilter(f)}
                  className={cx("min-h-9 rounded-md px-3 text-sm font-medium transition", filter === f ? "bg-card text-foreground shadow-card" : "text-muted-foreground hover:text-foreground")}
                >
                  {labels[f]} <span className="tabular-nums opacity-60">{counts[f]}</span>
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3">
              <label className="flex min-h-9 items-center gap-2 text-sm text-muted-foreground">
                <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} className="size-4 accent-primary" />
                Hide done
              </label>
              {!readOnly && unassigned.length > 0 && (
                <Button
                  variant="outline"
                  onClick={() => setPicking({ title: `Who is doing the ${openUnassigned} unassigned ${shiftInfo?.label ?? ""} tasks?`, ids: unassigned.map((t) => t.id) })}
                  className="min-h-9 px-3 text-sm"
                >
                  <Users className="size-4" />
                  Assign {openUnassigned} unassigned
                </Button>
              )}
            </div>
          </div>

          {whole.length > 0 && left === 0 ? (
            <div key={`cheer-${listKey}`} ref={cheerBanner} className="pop relative mb-3 flex scroll-mt-3 items-center gap-3 rounded-xl bg-success/10 px-4 py-3 font-medium text-success">
              <span aria-hidden className={cx("brand-mark brand-palm h-7 shrink-0", party === listKey && "sway")} />
              {shiftInfo?.label} is all done.{!readOnly && ` ${cheer(listKey)}`}
              {party === listKey &&
                Array.from({ length: 22 }, (_, i) => {
                  const angle = (i / 22) * 2 * Math.PI;
                  const reach = 70 + ((i * 37) % 80);
                  const style = { "--dx": `${Math.cos(angle) * reach * 1.6}px`, "--dy": `${Math.sin(angle) * reach - 30}px`, "--rot": `${(i % 2 ? 1 : -1) * (200 + i * 20)}deg`, "--c": CONFETTI[i % CONFETTI.length], "--delay": `${(i % 5) * 25}ms` };
                  return <span key={i} aria-hidden className="confetti" style={style as React.CSSProperties} />;
                })}
            </div>
          ) : (
            <p className={cx("mb-2 text-sm", overdue ? "font-medium text-destructive" : "text-muted-foreground")}>
              {whole.length > 0 && `${left} left · ${overdue ? "was due" : "due"} ${time(shiftInfo?.due_at)}`}
            </p>
          )}
          {clickupDay && (
            <div className="mb-2">
              <Notice>
                {readOnly
                  ? "This day was done in ClickUp. It is shown here for reference."
                  : "ClickUp still runs this day. Try things out here: ticks, people, photos and notes are put back as ClickUp has them tomorrow, or as soon as the task changes in ClickUp."}
                {hasPlanned && " Tasks marked Planned are on the schedule, but ClickUp adds each one only once its previous copy is due. They can be ticked from then on."}
              </Notice>
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
              className="mb-3 flex min-h-14 w-full items-center gap-3 rounded-xl border border-border bg-card px-4 py-2 text-left shadow-card transition hover:border-ring hover:bg-accent active:scale-[0.99]"
            >
              <ClipboardList className={cx("size-5 shrink-0", day.count_submitted ? "text-success" : "text-tone-ink")} />
              <span className="flex-1">
                <span className="block font-medium">Closing count</span>
                <span className="block text-sm text-muted-foreground">
                  {readOnly
                    ? "Sent in ClickUp. Open to see counts, notes and photos."
                    : clickupDay
                      ? day.count_submitted
                        ? "Sent. Open to view it. A count sent in ClickUp replaces it."
                        : "Try it out here. Inventory still reads the ClickUp count."
                      : day.count_submitted
                        ? "Sent. Open to view or correct it."
                        : "Not sent yet. Counts, notes and photos."}
                </span>
              </span>
              <ChevronRight className="size-5 text-muted-foreground" />
            </button>
          )}

          <ul key={listKey} className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card shadow-card">
            {top.filter(shows).map((t, i) => (
              <li key={t.id} className="rise" style={{ "--i": i } as React.CSSProperties}>
                {row(t)}
                {isOpen(t) && childrenOf(t.id).filter(matches).length > 0 && (
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
                  ? "Nothing on this list yet."
                  : words.length
                    ? `No ${shiftInfo?.label ?? ""} task matches “${query.trim()}”.`
                  : filter === "mine"
                    ? "Nothing here is yours yet. Tap the person icon on a task to take it."
                    : hideDone && left === 0
                      ? "All done for this shift."
                      : "Nothing to show."}
              </li>
            )}
            {!clickupDay && !shop && (
              <li>
                <button
                  onClick={() => activeShift && setForm({ title: "Add a task", initial: blankDraft(activeShift), once: true })}
                  className="group flex min-h-13 w-full items-center gap-3 px-4 text-left font-medium text-primary hover:bg-accent"
                >
                  <Plus className="size-5 transition-transform group-hover:rotate-90" />
                  Add a task
                </button>
              </li>
            )}
          </ul>
          {elsewhere.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              Also matches on
              {elsewhere.map((s) => (
                <button
                  key={s.id}
                  onClick={() => {
                    setShift(s.id);
                    setOpen(null);
                  }}
                  className="flex min-h-10 items-center gap-1 rounded-full border border-border bg-card px-3 font-medium text-foreground transition hover:bg-muted active:scale-95"
                >
                  {s.label} <span className="tabular-nums text-muted-foreground">{s.n}</span>
                  <ChevronRight className="size-4" />
                </button>
              ))}
            </div>
          )}
        </main>

        {wide && (
          <aside className="w-[26rem] shrink-0 overflow-y-auto border-l border-border bg-card xl:w-[30rem]">
            {openTask ? (
              <div key={openTask.id} className="slide-in p-6">
                <div className="mb-4 flex items-start justify-between gap-3">
                  <h2 className="text-xl font-semibold leading-snug">{openTask.title}</h2>
                  <button aria-label="Close" onClick={() => setOpen(null)} className="-m-2 rounded-full p-2 text-muted-foreground hover:bg-muted">
                    <X className="size-5" />
                  </button>
                </div>
                {detail}
              </div>
            ) : (
              <div className="fade flex h-full flex-col items-center justify-center gap-3 p-10 text-center text-muted-foreground">
                <span className="flex size-14 items-center justify-center rounded-full bg-tone-soft text-tone-ink">
                  <MousePointerClick className="size-6" />
                </span>
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
      {form && (
        <TaskForm
          boot={boot}
          session={session}
          date={date}
          title={form.title}
          initial={form.initial}
          once={form.once}
          onClose={() => setForm(null)}
          onSaved={async (saved) => {
            setForm(null);
            setShift(saved.shift);
            setToast(saved.message);
            await refresh();
            setOpen(saved.taskId);
          }}
        />
      )}
      {toast && <Toast key={toast} text={toast} />}
      <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={(e) => tookPhoto(e.target.files?.[0])} />
      {counting && (
        <CountForm
          boot={boot}
          session={session}
          date={date}
          names={names}
          readOnly={readOnly}
          tryout={clickupDay}
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
  expanded?: boolean;
  onExpand: () => void;
  names: Map<string, string>;
  selected: boolean;
  readOnly: boolean;
  onToggle: (t: Task) => void;
  onOpen: (id: string) => void;
  onAssign: (t: Task) => void;
}

const isPlanned = (t: Task) => t.source === "planned";

function TaskRow({ task, sub, steps, expanded, onExpand, names, selected, readOnly, onToggle, onOpen, onAssign }: RowProps) {
  const done = task.status === "done";
  const assignee = task.assignee_id ? names.get(task.assignee_id) : undefined;
  const photos = task.photos?.length ?? 0;
  const notes = task.comments?.length ?? 0;
  const meta = "flex items-center gap-1";
  return (
    <div className={cx("flex items-stretch transition-colors", sub && "pl-6 sm:pl-8", selected && "bg-tone-soft shadow-[inset_3px_0_0_var(--tone)]")}>
      <button
        aria-label={done ? `Undo ${task.title}` : `Done: ${task.title}`}
        aria-pressed={done}
        disabled={readOnly}
        onClick={() => onToggle(task)}
        className="flex w-14 shrink-0 items-center justify-center disabled:opacity-60 sm:w-16"
      >
        <Tick done={done} dim={task.pending} />
      </button>
      <button onClick={() => onOpen(task.id)} className={cx("flex min-h-15 min-w-0 flex-1 flex-col justify-center gap-0.5 py-2.5 pr-2 text-left", !selected && "hover:bg-muted/70")}>
        <span className="text-[16px] leading-snug">
          <span className="strike" data-done={done}>
            {task.title}
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[13px] text-muted-foreground empty:hidden">
          {isPlanned(task) && (
            <span className={cx(meta, "rounded-full bg-muted px-1.5 font-medium")}>
              <CalendarClock className="size-3.5" />
              Planned
            </span>
          )}
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
          {task.needs_photo && photos === 0 ? (
            <span className={cx(meta, "rounded-full px-1.5 font-medium", done ? "bg-destructive/10 text-destructive" : "bg-tone-soft text-tone-ink")}>
              <Camera className="size-3.5" />
              {done ? "No photo" : "Photo needed"}
            </span>
          ) : (
            photos > 0 && (
              <span className={meta}>
                <Camera className="size-3.5" />
                {photos}
              </span>
            )
          )}
          {task.examples && task.examples.photos + task.examples.videos > 0 && (
            <span className={meta} title="Has examples of how it should look">
              {task.examples.videos > 0 ? <Clapperboard className="size-3.5" /> : <Images className="size-3.5" />}
              {task.examples.videos > 0 ? "Video" : "Example"}
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
      {expanded !== undefined && steps.length > 0 && (
        <button
          aria-expanded={expanded}
          aria-label={`${expanded ? "Hide" : "Show"} the ${steps.length} steps of ${task.title}`}
          onClick={onExpand}
          className="flex shrink-0 items-center gap-1 rounded-lg px-2 text-[13px] font-medium text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <span className="hidden sm:inline">{expanded ? "Hide" : "Show"} steps</span>
          <ChevronDown className={cx("size-5 transition-transform", expanded && "rotate-180")} />
        </button>
      )}
      <button
        disabled={readOnly}
        onClick={() => onAssign(task)}
        aria-label={assignee ? `Doing it: ${assignee}. Change` : "Pick who is doing it"}
        className="group flex shrink-0 items-center gap-2 px-3 sm:px-4"
      >
        {task.assignee_id && assignee ? (
          <>
            <span className="hidden max-w-28 truncate text-sm text-muted-foreground md:inline">{firstName(assignee)}</span>
            <span key={task.assignee_id} className="pop inline-flex transition-transform group-enabled:group-hover:scale-110">
              <Avatar id={task.assignee_id} name={assignee} />
            </span>
          </>
        ) : (
          <span className={cx("flex size-8 items-center justify-center rounded-full border border-dashed border-ring text-muted-foreground transition group-enabled:group-hover:border-primary group-enabled:group-hover:bg-accent group-enabled:group-hover:text-primary", readOnly && "opacity-0")}>
            <UserPlus className="size-4" />
          </span>
        )}
      </button>
    </div>
  );
}
