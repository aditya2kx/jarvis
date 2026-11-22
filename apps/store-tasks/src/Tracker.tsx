import { CalendarDays, ListChecks, MessageSquare, Pencil, Plus, Settings, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, device, isOffline, type Project, type TrackerDraft, type TrackerItem } from "./api";
import { GoogleSignIn } from "./App";
import { FeedbackButton } from "./Feedback";
import { AddRow, DetailPane, DoneBar, FilterBar, matchesWords, Notes, SearchBox, searchWords, Section, TaskRow, WhoButton } from "./parts";
import { PersonPicker } from "./People";
import { ProjectSwitch, usePlaces } from "./Projects";
import { setWhere, track } from "./steps";
import { Avatar, Button, cx, dayLabel, Field, firstName, Input, inputClass, Notice, Sheet, time, useWide } from "./ui";

const said = (e: unknown, offline: string) => (isOffline(e) ? offline : (e as Error).message);
const when = (iso: string) =>
  `${new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "America/Chicago" })}, ${time(iso)}`;
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Chicago" });
const points = (detail: string) => detail.split("\n").filter((l) => l.trim()).length;
/** Enough rows to show the whole text on a phone without scrolling inside the box. */
const rowsFor = (text: string, max: number) => Math.min(max, Math.max(2, text.split("\n").reduce((n, l) => n + Math.max(1, Math.ceil(l.length / 34)), 0)));

/** A project's own front door: sign in with Google, or be told this account is not on it. */
function Door({ id, notice }: { id: string; notice: string }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-primary p-6 text-primary-foreground">
      <div className="w-full max-w-xs space-y-8 text-center">
        <div className="rise">
          <span role="img" aria-label="Palmetto Superfoods" className="brand-mark brand-word mx-auto block w-60" />
          <p className="mt-4 text-[15px] text-primary-foreground/80">Sign in with your own Google account to see this project</p>
        </div>
        {notice && <Notice tone="error">{notice}</Notice>}
        <GoogleSignIn next={`/p/${id}`} />
      </div>
    </div>
  );
}

const FILTERS = ["all", "mine", "unassigned"] as const;
type Filter = (typeof FILTERS)[number];
const FILTER_LABEL: Record<Filter, string> = { all: "All", mine: "Mine", unassigned: "Unassigned" };

/** A private project: the same list, detail and people picker as a store's day, apart from the stores. */
export function ProjectScreen({ id, signInError }: { id: string; signInError: string }) {
  const [data, setData] = useState<Project | null>(null);
  const [door, setDoor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("mine");
  // A project never starts over like a day does, so what is done stays out of the way unless asked for.
  const [hideDone, setHideDone] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [picking, setPicking] = useState<TrackerItem | null>(null);
  const [adding, setAdding] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [justDone, setJustDone] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState("");
  const wide = useWide();
  const places = usePlaces();
  const names = useMemo(() => new Map((data?.members ?? []).map((m) => [m.id, m.name])), [data?.members]);

  const load = useCallback(() => {
    api
      .project(id)
      .then((p) => {
        if (p.device) device.set(p.device);
        setData(p);
        setDoor(null);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) {
          // Signed in, but not on this project: offer another Google account.
          device.set(null);
          setDoor("This Google account is not on this project. Sign in with the one you were added with.");
        } else if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          device.set(null);
          setDoor(signInError);
        } else setError(said(e, "No connection. The project needs Wi-Fi."));
      });
  }, [id, signInError]);
  useEffect(load, [load]);
  useEffect(() => setWhere({ screen: `Project · ${FILTER_LABEL[filter]}`, task_id: open ?? undefined }), [filter, open]);

  const put = (item: TrackerItem) => setData((d) => d && { ...d, items: d.items.map((i) => (i.id === item.id ? item : i)) });
  const save = async (item: TrackerItem, patch: Partial<TrackerDraft>, offline: string) => {
    setError("");
    put({ ...item, ...patch });
    try {
      put(await api.projectUpdate(id, item.id, patch));
    } catch (e) {
      put(item);
      setError(said(e, offline));
    }
  };

  const toggle = async (item: TrackerItem) => {
    const status = item.status === "open" ? "done" : "open";
    track("action", `${status === "done" ? "tick" : "undo"} project task ${item.title}`);
    setError("");
    if (status === "done") navigator.vibrate?.(12);
    put({ ...item, status, done_by: data?.me ?? null, done_at: new Date().toISOString() });
    setJustDone((s) => new Set(s).add(item.id));
    try {
      put(await api.projectUpdate(id, item.id, { status }));
    } catch (e) {
      put(item);
      setError(said(e, "No connection. That tick was not saved."));
    }
  };

  if (door !== null) return <Door id={id} notice={door} />;
  if (!data) {
    return (
      <div className="flex min-h-full items-center justify-center p-6">
        {error ? <Notice tone="error">{error}</Notice> : <p className="text-muted-foreground">Loading…</p>}
      </div>
    );
  }

  const me = data.me;
  const words = searchWords(query);
  const found = (i: TrackerItem) => matchesWords(words, i.title, i.detail);
  const inFilter = (i: TrackerItem, f: Filter) => f === "all" || (f === "mine" ? i.owners.includes(me) : !i.owners.length);
  const visible = (i: TrackerItem) => !hideDone || i.status === "open" || justDone.has(i.id);
  const counts = Object.fromEntries(FILTERS.map((f) => [f, data.items.filter((i) => found(i) && visible(i) && inFilter(i, f)).length])) as Record<Filter, number>;
  // Grouped by who is doing it, in the order the members are listed; no one last.
  const order = data.members.map((m) => m.id);
  const rank = (i: TrackerItem) => (i.owners.length ? Math.min(...i.owners.map((o) => order.indexOf(o))) * 10 + i.owners.length : 1e6);
  const shown = data.items.filter((i) => found(i) && visible(i) && inFilter(i, filter)).sort((a, b) => rank(a) - rank(b));
  const left = data.items.filter((i) => i.status === "open" && inFilter(i, filter)).length;
  const item = data.items.find((i) => i.id === open) ?? null;
  const switchable = places.length > 1;
  const seenBy = `Only ${data.members.map((m) => firstName(m.name)).join(", ")} can see this`;
  const meName = names.get(me) ?? "";

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col">
      <header className="border-b border-border bg-card px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-6">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            {switchable ? (
              <h1 className="min-w-0">
                <ProjectSwitch places={places} current={data.id} label={data.name} />
              </h1>
            ) : (
              <>
                <span aria-hidden className="brand-mark brand-palm hidden h-9 shrink-0 text-primary sm:inline-block" />
                <h1 className="truncate text-xl font-extrabold leading-tight tracking-tight">{data.name}</h1>
              </>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <FeedbackButton project={data.id} />
            {data.admin && (
              <button aria-label="People on this project" onClick={() => setSharing(true)} className="rounded-full p-2 text-muted-foreground hover:bg-muted sm:p-2.5">
                <Settings className="size-5" />
              </button>
            )}
            <span aria-label={meName} className="hidden items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-3 text-[15px] font-medium sm:flex">
              <Avatar id={me} name={meName} />
              {firstName(meName)}
            </span>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-y-auto px-4 pb-10 pt-3 sm:px-6">
          <SearchBox query={query} onChange={setQuery} onDone={() => query.trim() && track("action", `search "${query.trim()}"`)} />
          <FilterBar options={FILTERS.map((f) => ({ id: f, label: FILTER_LABEL[f], n: counts[f] }))} value={filter} onChange={setFilter} hideDone={hideDone} onHideDone={setHideDone} />
          <p className="mb-2 text-sm text-muted-foreground">
            {left} left · {seenBy}
          </p>
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

          <ul key={filter} className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card shadow-card">
            {shown.map((i, n) => (
              <li key={i.id} className="rise" style={{ "--i": n } as React.CSSProperties}>
                <ProjectRow
                  item={i}
                  names={names}
                  selected={wide && i.id === open}
                  onToggle={() => toggle(i)}
                  onOpen={() => setOpen(open === i.id && wide ? null : i.id)}
                  onAssign={() => setPicking(i)}
                />
              </li>
            ))}
            {shown.length === 0 && (
              <li className="px-4 py-12 text-center text-muted-foreground">
                {data.items.length === 0
                  ? "Nothing on this list yet."
                  : words.length
                    ? `No task matches “${query.trim()}”.`
                    : filter === "mine"
                      ? "Nothing open for you. Tap the person icon on a task to take it."
                      : hideDone && left === 0
                        ? "All done."
                        : "Nothing to show."}
              </li>
            )}
            <AddRow onClick={() => setAdding(true)} />
          </ul>
        </main>

        <DetailPane wide={wide} title={item?.title ?? null} empty="Pick a task to see its details, who is doing it and its notes." onClose={() => setOpen(null)}>
          {item && (
            <ProjectDetail
              key={item.id}
              project={data}
              item={item}
              names={names}
              onSave={(patch, offline) => save(item, patch, offline)}
              onChanged={put}
              onToggle={() => toggle(item)}
              onAssign={() => setPicking(item)}
              onRemoved={() => {
                setOpen(null);
                load();
              }}
            />
          )}
        </DetailPane>
      </div>

      {picking && (
        <PersonPicker
          title={`Who is doing “${picking.title}”?`}
          staff={data.members}
          me={me}
          onPick={() => undefined}
          onClose={() => setPicking(null)}
          footnote="Everyone on this project. Someone missing? Whoever runs it adds them with the gear."
          many={{
            picked: picking.owners,
            onSave: (owners) => {
              track("action", `owners of ${picking.title}`);
              save(picking, { owners }, "No connection. Who is doing it was not saved.");
              setPicking(null);
            },
          }}
        />
      )}
      {adding && (
        <AddSheet
          project={data}
          onAdded={() => {
            setAdding(false);
            setFilter("all");
            load();
          }}
          onClose={() => setAdding(false)}
        />
      )}
      {sharing && (
        <MembersSheet
          project={data}
          onSaved={(p) => {
            setData(p);
            setSharing(false);
          }}
          onClose={() => setSharing(false)}
        />
      )}
    </div>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      aria-pressed={on}
      onClick={onClick}
      className={cx(
        "flex min-h-10 items-center gap-2 rounded-full border px-3 text-[15px] font-medium transition active:scale-95",
        on ? "border-primary bg-accent text-accent-foreground" : "border-border bg-card text-muted-foreground hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}

const flip = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

interface RowProps {
  item: TrackerItem;
  names: Map<string, string>;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onAssign: () => void;
}

function ProjectRow({ item, names, selected, onToggle, onOpen, onAssign }: RowProps) {
  const done = item.status === "done";
  const sub = points(item.detail);
  const late = !done && item.due !== null && item.due < today();
  const meta = "flex items-center gap-1";
  return (
    <TaskRow
      title={item.title}
      done={done}
      selected={selected}
      people={item.owners.map((o) => ({ id: o, name: names.get(o) ?? "?" }))}
      onToggle={onToggle}
      onOpen={onOpen}
      onAssign={onAssign}
      meta={
        <>
          {done && item.done_at && <span className={cx(meta, "text-success")}>{[firstName(names.get(item.done_by ?? "")), when(item.done_at)].filter(Boolean).join(" · ")}</span>}
          {item.tags.map((t) => (
            <span key={t} className="rounded-full bg-muted px-1.5 font-medium">
              {t}
            </span>
          ))}
          {item.due && (
            <span className={cx(meta, late && "font-medium text-destructive")}>
              <CalendarDays className="size-3.5" />
              {late ? "Was due" : "Due"} {dayLabel(item.due)}
            </span>
          )}
          {sub > 0 && (
            <span className={meta}>
              <ListChecks className="size-3.5" />
              {sub} {sub === 1 ? "point" : "points"}
            </span>
          )}
          {item.notes.length > 0 && (
            <span className={meta}>
              <MessageSquare className="size-3.5" />
              {item.notes.length}
            </span>
          )}
        </>
      }
    />
  );
}

interface DetailProps {
  project: Project;
  item: TrackerItem;
  names: Map<string, string>;
  onSave: (patch: Partial<TrackerDraft>, offline: string) => Promise<void>;
  onChanged: (item: TrackerItem) => void;
  onToggle: () => void;
  onAssign: () => void;
  onRemoved: () => void;
}

/** Laid out like a store task's detail: done, who, details, store and due date, notes. */
function ProjectDetail({ project, item, names, onSave, onChanged, onToggle, onAssign, onRemoved }: DetailProps) {
  const { me, tags } = project;
  const [editing, setEditing] = useState<{ title: string; detail: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const done = item.status === "done";
  const owners = item.owners.map((o) => ({ id: o, name: names.get(o) ?? "?" }));

  const saveText = async () => {
    if (!editing?.title.trim()) return;
    setBusy(true);
    await onSave(editing, "No connection. Your changes are not saved yet.");
    setBusy(false);
    setEditing(null);
  };
  const sendNote = async (text: string) => {
    setError("");
    try {
      onChanged(await api.projectNote(project.id, item.id, text));
    } catch (e) {
      setError(said(e, "No connection. The note was not saved."));
      throw e;
    }
  };
  const remove = async () => {
    if (!window.confirm("Delete this task?")) return;
    setError("");
    try {
      await api.projectRemove(project.id, item.id);
      onRemoved();
    } catch (e) {
      setError(said(e, "No connection. It was not deleted."));
    }
  };

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Added by {firstName(names.get(item.created_by)) || "someone"} on {when(item.created_at)}
        </p>
        {error && <Notice tone="error">{error}</Notice>}
        <DoneBar
          done={done}
          readOnly={false}
          onToggle={onToggle}
          said={`Done${item.done_by ? ` by ${firstName(names.get(item.done_by)) || "someone"}` : ""}${item.done_at ? ` on ${when(item.done_at)}` : ""}`}
        />
      </div>

      <Section title="Who is doing it">
        <div className="flex gap-2">
          <WhoButton people={owners} readOnly={false} onClick={onAssign} />
          {!item.owners.includes(me) && (
            <Button variant="outline" onClick={() => onSave({ owners: [...item.owners, me] }, "No connection. Who is doing it was not saved.")} className="min-h-12 shrink-0">
              I'll do it
            </Button>
          )}
        </div>
      </Section>

      <Section
        title="Details"
        action={
          editing === null ? (
            <button onClick={() => setEditing({ title: item.title, detail: item.detail })} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-primary hover:bg-accent">
              <Pencil className="size-3.5" />
              Edit
            </button>
          ) : undefined
        }
      >
        {editing !== null ? (
          <div className="space-y-2">
            <Field label="What needs doing">
              <textarea rows={rowsFor(editing.title, 6)} value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} className={cx(inputClass, "py-2 leading-snug")} />
            </Field>
            <Field label="Points">
              <textarea
                rows={rowsFor(editing.detail, 12)}
                value={editing.detail}
                onChange={(e) => setEditing({ ...editing, detail: e.target.value })}
                placeholder="One point per line"
                className={cx(inputClass, "py-2 leading-relaxed")}
              />
            </Field>
            <div className="flex gap-2">
              <Button disabled={busy || !editing.title.trim()} onClick={saveText}>
                {busy ? "Saving…" : "Save"}
              </Button>
              <Button variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : item.detail ? (
          <p className="whitespace-pre-wrap break-words text-[16px] leading-relaxed">{item.detail}</p>
        ) : (
          <p className="text-muted-foreground">No details yet.</p>
        )}
      </Section>

      <Section title={tags.length ? "Store and due date" : "Due date"}>
        <div className="flex flex-wrap items-center gap-2">
          {tags.map((t) => (
            <Chip key={t} on={item.tags.includes(t)} onClick={() => onSave({ tags: tags.filter((x) => flip(item.tags, t).includes(x)) }, "No connection. The store was not saved.")}>
              {t}
            </Chip>
          ))}
          <Input
            type="date"
            aria-label="Due date"
            value={item.due ?? ""}
            onChange={(e) => onSave({ due: e.target.value || null }, "No connection. The due date was not saved.")}
            className="w-44"
          />
        </div>
      </Section>

      <Notes notes={item.notes} names={names} readOnly={false} when={when} onSend={sendNote} />

      {(project.admin || item.created_by === me) && (
        <div className="flex flex-wrap gap-2 border-t border-border pt-5">
          <Button variant="danger" onClick={remove}>
            <Trash2 className="size-4" />
            Delete task
          </Button>
        </div>
      )}
    </div>
  );
}

interface AddSheetProps {
  project: Project;
  onAdded: () => void;
  onClose: () => void;
}

/** Type or paste, check how it was split up, then add. */
function AddSheet({ project, onAdded, onClose }: AddSheetProps) {
  const { members } = project;
  const [text, setText] = useState("");
  const [drafts, setDrafts] = useState<TrackerDraft[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const example = members.length > 1 ? `${firstName(members[0].name)} + ${firstName(members[1].name)}` : "a name";

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(said(e, "No connection. Nothing was added; try again when Wi-Fi is back."));
    } finally {
      setBusy(false);
    }
  };
  const check = () =>
    run(async () => {
      const { items } = await api.projectParse(project.id, text);
      if (!items.length) throw new Error("No tasks found. Put each task on its own line.");
      setDrafts(items);
    });
  const add = () =>
    run(async () => {
      await api.projectAdd(project.id, drafts ?? []);
      track("action", `add ${drafts?.length} project tasks`);
      onAdded();
    });
  const edit = (n: number, patch: Partial<TrackerDraft>) => setDrafts((d) => d && d.map((x, i) => (i === n ? { ...x, ...patch } : x)));

  if (!drafts) {
    return (
      <Sheet title="Add tasks" onClose={onClose}>
        <div className="space-y-4">
          <textarea
            rows={9}
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Tasks, one per line, or pasted meeting notes"
            placeholder={`One task per line, or paste your meeting notes.\n\n${example}\n1. First task\n2. Second task`}
            className={cx(inputClass, "py-2.5 leading-relaxed")}
          />
          <p className="text-sm text-muted-foreground">A line with only names ({example}) sets who is doing the tasks under it. Without one, they are yours. Indented lines become points of the task above.</p>
          {error && <Notice tone="error">{error}</Notice>}
          <Button className="min-h-12 w-full text-base" disabled={busy || !text.trim()} onClick={check}>
            {busy ? "Checking…" : "Check the tasks"}
          </Button>
        </div>
      </Sheet>
    );
  }
  return (
    <Sheet title={`Add ${drafts.length} ${drafts.length === 1 ? "task" : "tasks"}`} onClose={onClose}>
      <div className="space-y-4">
        <ul className="space-y-3">
          {drafts.map((d, n) => (
            <li key={n} className="space-y-2 rounded-xl border border-border p-3">
              <div className="flex items-start gap-2">
                <textarea rows={rowsFor(d.title, 6)} aria-label="Task" value={d.title} onChange={(e) => edit(n, { title: e.target.value })} className={cx(inputClass, "flex-1 py-2 leading-snug")} />
                <button aria-label="Leave this one out" onClick={() => setDrafts(drafts.filter((_, i) => i !== n))} className="rounded-full p-2.5 text-muted-foreground hover:bg-muted">
                  <X className="size-4" />
                </button>
              </div>
              {d.detail && <p className="whitespace-pre-wrap pl-1 text-sm text-muted-foreground">{d.detail}</p>}
              <div className="flex flex-wrap gap-2">
                {members.map((m) => (
                  <Chip key={m.id} on={d.owners.includes(m.id)} onClick={() => edit(n, { owners: flip(d.owners, m.id) })}>
                    {firstName(m.name)}
                  </Chip>
                ))}
                {d.tags.map((t) => (
                  <span key={t} className="flex min-h-10 items-center rounded-full bg-muted px-3 text-sm font-medium text-muted-foreground">
                    {t}
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setDrafts(null)}>
            Back
          </Button>
          <Button className="min-h-12 flex-1 text-base" disabled={busy || drafts.length === 0 || drafts.some((d) => !d.title.trim())} onClick={add}>
            {busy ? "Adding…" : `Add ${drafts.length} ${drafts.length === 1 ? "task" : "tasks"}`}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}

interface MembersSheetProps {
  project: Project;
  onSaved: (p: Project) => void;
  onClose: () => void;
}

/** Who is on the project, by their own Google account. Whoever runs it always stays. */
function MembersSheet({ project, onSaved, onClose }: MembersSheetProps) {
  const [people, setPeople] = useState(project.members.map((m) => ({ id: m.id, name: m.name, emails: m.emails ?? [] })));
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ready = name.trim() && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const changed = JSON.stringify(people) !== JSON.stringify(project.members.map((m) => ({ id: m.id, name: m.name, emails: m.emails ?? [] })));

  const add = () => {
    setPeople((p) => [...p, { id: "", name: name.trim(), emails: [email.trim().toLowerCase()] }]);
    setName("");
    setEmail("");
  };
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      onSaved(await api.projectMembers(project.id, people.map(({ id, ...p }) => (id ? { id, ...p } : p))));
    } catch (e) {
      setError(said(e, "No connection. Nothing was changed."));
      setBusy(false);
    }
  };

  return (
    <Sheet title="People on this project" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Each person signs in with their own Google account. Store staff and the shop iPad never see this project.</p>
        <ul className="divide-y divide-border rounded-xl border border-border">
          {people.map((p) => (
            <li key={p.id || p.emails[0]} className="flex min-h-14 items-center gap-3 px-3 py-2">
              <Avatar id={p.id || p.emails[0]} name={p.name} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[16px]">{p.name}</span>
                <span className="block truncate text-sm text-muted-foreground">{p.emails.join(", ")}</span>
              </span>
              {project.admins.includes(p.id) ? (
                <span className="text-sm text-muted-foreground">Runs it</span>
              ) : (
                <button aria-label={`Remove ${p.name}`} onClick={() => setPeople((all) => all.filter((x) => x !== p))} className="rounded-full p-2.5 text-muted-foreground hover:bg-muted">
                  <X className="size-4" />
                </button>
              )}
            </li>
          ))}
        </ul>
        <div className="space-y-2">
          <p className="text-sm font-medium text-muted-foreground">Add someone</p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} className="sm:w-40" />
            <Input type="email" placeholder="Their Google account email" value={email} onChange={(e) => setEmail(e.target.value)} className="flex-1" />
            <Button variant="outline" disabled={!ready} onClick={add}>
              <Plus className="size-4" />
              Add
            </Button>
          </div>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <Button className="min-h-12 w-full text-base" disabled={busy || !changed} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </Button>
      </div>
    </Sheet>
  );
}
