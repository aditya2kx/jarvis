import { Camera, Check, ChevronLeft, ChevronRight, ClipboardList, CloudOff, Lock, Plus, Settings } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isOffline, type Bootstrap, type Day, type Session, type Task } from "./api";
import { CountForm } from "./Count";
import { applyPending, flush, loadQueue, push, type Pending } from "./queue";
import { addDays, Button, cx, dayLabel, Field, Input, inputClass, Notice, Sheet, time } from "./ui";

const REFRESH_MS = 60 * 1000;
const MAX_PHOTO_EDGE = 1600;

const cacheKey = (date: string) => `st.day.${date}`;
const readCache = (date: string): Day | null => JSON.parse(localStorage.getItem(cacheKey(date)) ?? "null");

/** The shift people are most likely working on now: the first one not yet due, else the last. */
function currentShift(day: Day): string {
  const now = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago" });
  return (day.shifts.find((s) => now <= s.due) ?? day.shifts[day.shifts.length - 1]).id;
}

async function shrink(file: File): Promise<Blob> {
  const img = await createImageBitmap(file);
  const scale = Math.min(1, MAX_PHOTO_EDGE / Math.max(img.width, img.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not read photo"))), "image/jpeg", 0.82));
}

interface Props {
  boot: Bootstrap;
  session: Session;
  /** Null on a person's own Google account: there is nobody to hand the screen to. */
  onLock: (() => void) | null;
  onManage: () => void;
}

export function DayScreen({ boot, session, onLock, onManage }: Props) {
  const [date, setDate] = useState(boot.today);
  const [server, setServer] = useState<Day | null>(() => readCache(boot.today));
  const [queue, setQueue] = useState<Pending[]>(loadQueue);
  const [shift, setShift] = useState<string | null>(null);
  const [hideDone, setHideDone] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [counting, setCounting] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const isManager = session.staff.role === "manager";
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
  const openTask = day?.tasks.find((t) => t.id === open) ?? null;

  const toggle = (t: Task) => {
    if (t.source === "clickup") return;
    if (t.status === "open" && t.needs_photo && !t.photos?.length && !isManager) {
      setOpen(t.id);
      return;
    }
    if (t.status === "done" && !isManager && t.completed_by !== session.staff.id) {
      setError(`${names.get(t.completed_by ?? "") ?? "Someone else"} ticked this. Ask them or a manager to undo it.`);
      return;
    }
    setError("");
    setQueue(push({ id: t.id, kind: t.status === "open" ? "complete" : "reopen", at: new Date().toISOString(), token: session.token, staff_id: session.staff.id }));
    refresh();
  };

  const replaceTask = (t: Task) => setServer((d) => (d ? { ...d, tasks: d.tasks.map((x) => (x.id === t.id ? t : x)) } : d));

  if (!day) {
    return (
      <div className="flex min-h-full items-center justify-center p-6 text-muted-foreground">
        {offline ? "No connection, and this day is not saved on the device yet." : "Loading…"}
      </div>
    );
  }

  const readOnly = !day.generated;
  const top = tasks.filter((t) => !t.parent_id || !tasks.some((p) => p.id === t.parent_id));
  const children = (id: string) => tasks.filter((t) => t.parent_id === id);
  const visible = (t: Task) => !hideDone || t.status === "open" || children(t.id).some((c) => c.status === "open");
  const team = day.teams[activeShift ?? ""] ?? [];
  const isLastShift = activeShift === day.shifts.at(-1)?.id;

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col">
      <header className="space-y-3 px-4 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-6">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <button aria-label="Previous day" onClick={() => setDate(addDays(date, -1))} className="rounded-full p-2 text-muted-foreground active:bg-muted">
              <ChevronLeft className="size-5" />
            </button>
            <button onClick={() => setDate(boot.today)} className="rounded-lg px-1 text-left">
              <p className="text-xs text-muted-foreground">{boot.store.name}</p>
              <h1 className="text-lg font-semibold leading-tight">{date === day.today ? "Today" : dayLabel(date)}</h1>
            </button>
            <button
              aria-label="Next day"
              disabled={date >= addDays(day.today, 7)}
              onClick={() => setDate(addDays(date, 1))}
              className="rounded-full p-2 text-muted-foreground active:bg-muted disabled:opacity-30"
            >
              <ChevronRight className="size-5" />
            </button>
          </div>
          <div className="flex items-center gap-1">
            {(offline || queue.length > 0) && (
              <span className="flex items-center gap-1.5 rounded-full bg-muted px-3 py-1.5 text-sm text-muted-foreground">
                <CloudOff className="size-4" />
                {queue.length ? `${queue.length} to send` : "Offline"}
              </span>
            )}
            {isManager && (
              <button aria-label="Manage" onClick={onManage} className="rounded-full p-2.5 text-muted-foreground active:bg-muted">
                <Settings className="size-5" />
              </button>
            )}
            {onLock ? (
              <Button variant="outline" onClick={onLock} className="pl-3">
                <Lock className="size-4" />
                {session.staff.name.split(" ")[0]}
              </Button>
            ) : (
              <span className="px-2 text-[15px] font-medium">{session.staff.name.split(" ")[0]}</span>
            )}
          </div>
        </div>

        <div role="tablist" className="grid gap-2" style={{ gridTemplateColumns: `repeat(${day.shifts.length}, minmax(0, 1fr))` }}>
          {day.shifts.map((s) => {
            const mine = day.tasks.filter((t) => t.shift === s.id);
            const done = mine.filter((t) => t.status === "done").length;
            const selected = s.id === activeShift;
            return (
              <button
                key={s.id}
                role="tab"
                aria-selected={selected}
                onClick={() => setShift(s.id)}
                className={cx("rounded-xl border px-3 py-2 text-left transition", selected ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card")}
              >
                <span className="block text-[15px] font-medium">{s.label}</span>
                <span className={cx("block text-sm tabular-nums", selected ? "text-primary-foreground/70" : "text-muted-foreground")}>
                  {done}/{mine.length} · by {time(s.due_at)}
                </span>
                <span className={cx("mt-1.5 block h-1 overflow-hidden rounded-full", selected ? "bg-primary-foreground/20" : "bg-muted")}>
                  <span
                    className={cx("block h-full rounded-full transition-all", done === mine.length && mine.length ? "bg-success" : selected ? "bg-primary-foreground" : "bg-foreground")}
                    style={{ width: `${mine.length ? (100 * done) / mine.length : 0}%` }}
                  />
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex min-h-9 items-center justify-between gap-3 text-sm text-muted-foreground">
          <p className="truncate">{team.length ? `On shift: ${team.map((m) => m.name.split(",").reverse().join(" ").trim().split(" ")[0]).join(", ")}` : ""}</p>
          <label className="flex shrink-0 items-center gap-2">
            <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} className="size-4 accent-foreground" />
            Hide done
          </label>
        </div>
        {readOnly && <Notice>This day was done in ClickUp. It is shown here for reference.</Notice>}
        {error && <Notice tone="error">{error}</Notice>}
      </header>

      <main className="flex-1 overflow-y-auto px-4 pb-28 pt-2 sm:px-6">
        {top.filter(visible).length === 0 && (
          <p className="py-16 text-center text-muted-foreground">{tasks.length ? "All done for this shift." : "Nothing on this list."}</p>
        )}
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card empty:hidden">
          {top.filter(visible).map((t) => (
            <li key={t.id}>
              <Row task={t} names={names} readOnly={readOnly} onToggle={toggle} onOpen={setOpen} />
              {children(t.id).filter((c) => !hideDone || c.status === "open").length > 0 && (
                <ul className="divide-y divide-border border-t border-border bg-muted/40 pl-8">
                  {children(t.id)
                    .filter((c) => !hideDone || c.status === "open")
                    .map((c) => (
                      <li key={c.id}>
                        <Row task={c} names={names} readOnly={readOnly} onToggle={toggle} onOpen={setOpen} />
                      </li>
                    ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </main>

      {!readOnly && (
        <footer className="pointer-events-none fixed inset-x-0 bottom-0 flex justify-center gap-3 p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <Button variant="outline" onClick={() => setAdding(true)} className="pointer-events-auto shadow-lg">
            <Plus className="size-4" />
            Add task
          </Button>
          {isLastShift && boot.store.count_items.length > 0 && (
            <Button onClick={() => setCounting(true)} className="pointer-events-auto shadow-lg">
              <ClipboardList className="size-4" />
              {day.count_submitted ? "Closing count sent" : "Closing count"}
            </Button>
          )}
        </footer>
      )}

      {openTask && (
        <TaskSheet
          task={openTask}
          names={names}
          session={session}
          readOnly={readOnly}
          onClose={() => setOpen(null)}
          onToggle={toggle}
          onChanged={replaceTask}
        />
      )}
      {adding && activeShift && (
        <AddTask
          shifts={day.shifts}
          shift={activeShift}
          onClose={() => setAdding(false)}
          onAdd={async (title, shiftId, instructions) => {
            await api.addTask(session.token, { title, shift: shiftId, date, instructions });
            setAdding(false);
            refresh();
          }}
        />
      )}
      {counting && (
        <CountForm
          boot={boot}
          session={session}
          date={date}
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
  names: Map<string, string>;
  readOnly: boolean;
  onToggle: (t: Task) => void;
  onOpen: (id: string) => void;
}

function Row({ task, names, readOnly, onToggle, onOpen }: RowProps) {
  const done = task.status === "done";
  const who = names.get(task.completed_by ?? "")?.split(" ")[0];
  return (
    <div className="flex items-stretch">
      <button
        aria-label={done ? `Undo ${task.title}` : `Done: ${task.title}`}
        aria-pressed={done}
        disabled={readOnly}
        onClick={() => onToggle(task)}
        className="flex w-16 shrink-0 items-center justify-center active:bg-muted disabled:opacity-60"
      >
        <span
          className={cx(
            "flex size-8 items-center justify-center rounded-full border-2 transition",
            done ? "border-success bg-success text-white" : "border-ring",
            task.pending && "opacity-60",
          )}
        >
          {done && <Check className="size-5" strokeWidth={3} />}
        </span>
      </button>
      <button onClick={() => onOpen(task.id)} className="flex min-h-16 flex-1 items-center justify-between gap-3 py-2.5 pr-4 text-left active:bg-muted">
        <span>
          <span className={cx("block text-[17px] leading-snug", done && "text-muted-foreground line-through")}>{task.title}</span>
          {done && (
            <span className="block text-sm text-muted-foreground">
              {[who, time(task.completed_at)].filter(Boolean).join(" · ")}
              {task.pending && " · not sent yet"}
            </span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-2 text-muted-foreground">
          {task.needs_photo && <Camera className={cx("size-5", Boolean(task.photos?.length) && "text-success")} aria-label="Photo needed" />}
          {task.instructions && <ChevronRight className="size-5" aria-label="Has instructions" />}
        </span>
      </button>
    </div>
  );
}

interface TaskSheetProps {
  task: Task;
  names: Map<string, string>;
  session: Session;
  readOnly: boolean;
  onClose: () => void;
  onToggle: (t: Task) => void;
  onChanged: (t: Task) => void;
}

function TaskSheet({ task, names, session, readOnly, onClose, onToggle, onChanged }: TaskSheetProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [urls, setUrls] = useState<Record<string, string>>({});
  const file = useRef<HTMLInputElement>(null);
  const photos = useMemo(() => task.photos ?? [], [task.photos]);

  useEffect(() => {
    let live = true;
    photos.forEach((p) => {
      api
        .photoUrl(session.token, p.path)
        .then((u) => live && setUrls((m) => ({ ...m, [p.path]: u })))
        .catch(() => undefined);
    });
    return () => {
      live = false;
    };
  }, [photos, session.token]);

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    setError("");
    try {
      onChanged(await api.photo(session.token, task.id, await shrink(f)));
    } catch (e) {
      setError(isOffline(e) ? "No connection. The photo was not saved — try again when Wi-Fi is back." : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const done = task.status === "done";
  const blocked = !done && task.needs_photo && !photos.length && session.staff.role !== "manager";

  return (
    <Sheet title={task.title} onClose={onClose}>
      <div className="space-y-4">
        {task.instructions && <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{task.instructions}</p>}
        {done && (
          <p className="text-sm text-muted-foreground">
            Done by {names.get(task.completed_by ?? "") ?? "someone"} at {time(task.completed_at)}
          </p>
        )}
        {photos.length > 0 && (
          <div className="grid grid-cols-3 gap-2">
            {photos.map((p) =>
              urls[p.path] ? (
                <img key={p.path} src={urls[p.path]} alt="Task photo" className="aspect-square w-full rounded-lg object-cover" />
              ) : (
                <div key={p.path} className="aspect-square animate-pulse rounded-lg bg-muted" />
              ),
            )}
          </div>
        )}
        {error && <Notice tone="error">{error}</Notice>}
        {blocked && <Notice>This one needs a photo before it can be ticked.</Notice>}
        {!readOnly && (
          <div className="flex gap-3">
            <input ref={file} type="file" accept="image/*" capture="environment" hidden onChange={(e) => upload(e.target.files?.[0])} />
            {(task.needs_photo || photos.length > 0) && (
              <Button variant="outline" disabled={busy} onClick={() => file.current?.click()} className="flex-1">
                <Camera className="size-4" />
                {busy ? "Saving…" : "Add photo"}
              </Button>
            )}
            <Button
              variant={done ? "outline" : "primary"}
              disabled={blocked || busy}
              onClick={() => {
                onToggle(task);
                onClose();
              }}
              className="flex-1"
            >
              {done ? "Undo" : "Mark done"}
            </Button>
          </div>
        )}
      </div>
    </Sheet>
  );
}

interface AddTaskProps {
  shifts: Day["shifts"];
  shift: string;
  onClose: () => void;
  onAdd: (title: string, shift: string, instructions: string) => Promise<void>;
}

function AddTask({ shifts, shift, onClose, onAdd }: AddTaskProps) {
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [shiftId, setShiftId] = useState(shift);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onAdd(title.trim(), shiftId, instructions.trim());
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
        <Field label="Shift">
          <select value={shiftId} onChange={(e) => setShiftId(e.target.value)} className={inputClass}>
            {shifts.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Details (optional)">
          <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3} className={cx(inputClass, "py-2")} />
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" disabled={!title.trim() || busy} className="w-full">Add</Button>
      </form>
    </Sheet>
  );
}
