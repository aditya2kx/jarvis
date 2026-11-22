import { CalendarDays, ListTree, MessageSquare, Plus, Trash2, Users, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, device, isOffline, type Project, type TrackerDraft, type TrackerItem } from "./api";
import { GoogleSignIn } from "./App";
import { ProjectSwitch, usePlaces } from "./Projects";
import { setWhere, track } from "./steps";
import { Avatar, Button, cx, dayLabel, Field, firstName, Input, inputClass, Notice, Sheet, Tick, time } from "./ui";

const VIEWS = ["mine", "everyone", "done"] as const;
type View = (typeof VIEWS)[number];
const VIEW_LABEL: Record<View, string> = { mine: "Mine", everyone: "Everyone", done: "Done" };

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

/** A private project: who owns what, what is still open, and the updates on each. Apart from the stores. */
export function ProjectScreen({ id, signInError }: { id: string; signInError: string }) {
  const [data, setData] = useState<Project | null>(null);
  const [door, setDoor] = useState<string | null>(null);
  const [view, setView] = useState<View>("mine");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [justDone, setJustDone] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState("");
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
  useEffect(() => setWhere({ screen: `Project · ${VIEW_LABEL[view]}`, task_id: open ?? undefined }), [view, open]);

  const put = (item: TrackerItem) => setData((d) => d && { ...d, items: d.items.map((i) => (i.id === item.id ? item : i)) });

  const toggle = async (item: TrackerItem) => {
    const status = item.status === "open" ? "done" : "open";
    track("action", `${status === "done" ? "tick" : "undo"} action item ${item.title}`);
    setError("");
    if (status === "done") navigator.vibrate?.(12);
    put({ ...item, status });
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
  const openItems = data.items.filter((i) => i.status === "open");
  const counts: Record<View, number> = {
    mine: openItems.filter((i) => i.owners.includes(me)).length,
    everyone: openItems.length,
    done: data.items.length - openItems.length,
  };
  const shown = data.items.filter((i) =>
    view === "done" ? i.status === "done" : (i.status === "open" || justDone.has(i.id)) && (view === "everyone" || i.owners.includes(me)),
  );
  // One group per set of owners, in the order the members are listed.
  const order = data.members.map((m) => m.id);
  const rank = (i: TrackerItem) => (i.owners.length ? Math.min(...i.owners.map((o) => order.indexOf(o))) * 10 + i.owners.length : 1e6);
  const groups = new Map<string, TrackerItem[]>();
  for (const i of [...shown].sort((a, b) => rank(a) - rank(b))) {
    const key = i.owners.join("+");
    groups.set(key, [...(groups.get(key) ?? []), i]);
  }
  const item = data.items.find((i) => i.id === open) ?? null;

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col">
      <header className="space-y-3 px-4 pt-[max(1rem,env(safe-area-inset-top))] sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
          <div className="min-w-0">
            {places.length > 1 ? (
              <h1>
                <ProjectSwitch places={places} current={data.id} label={data.name} big />
              </h1>
            ) : (
              <div className="flex items-center gap-3">
                <span aria-hidden className="brand-mark brand-palm block h-10 w-8 shrink-0 text-primary" />
                <h1 className="truncate text-2xl font-extrabold tracking-tight">{data.name}</h1>
              </div>
            )}
            <p className="mt-0.5 text-sm text-muted-foreground">Only {data.members.map((m) => firstName(m.name)).join(", ")} can see this.</p>
          </div>
          <div className="flex items-center gap-2">
            {data.admin && (
              <Button variant="outline" onClick={() => setSharing(true)}>
                <Users className="size-4" />
                People
              </Button>
            )}
            <Button onClick={() => setAdding(true)}>
              <Plus className="size-4" />
              Add items
            </Button>
          </div>
        </div>
        <div role="tablist" className="grid grid-cols-3 gap-1 rounded-xl bg-muted p-1">
          {VIEWS.map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={v === view}
              onClick={() => setView(v)}
              className={cx("min-h-10 rounded-lg text-[15px] font-medium", v === view ? "bg-card shadow-sm" : "text-muted-foreground")}
            >
              {VIEW_LABEL[v]} <span className="tabular-nums opacity-60">{counts[v]}</span>
            </button>
          ))}
        </div>
      </header>

      <main className="flex-1 space-y-5 overflow-y-auto px-4 pb-10 pt-4 sm:px-6">
        {error && <Notice tone="error">{error}</Notice>}
        {[...groups].map(([key, items]) => (
          <section key={key}>
            <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-muted-foreground">
              {items[0].owners.map((o) => (
                <Avatar key={o} id={o} name={names.get(o) ?? "?"} size="sm" />
              ))}
              {items[0].owners.length ? items[0].owners.map((o) => firstName(names.get(o))).join(" + ") : "No owner yet"}
              <span className="tabular-nums opacity-70">{items.length}</span>
            </h2>
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card shadow-card">
              {items.map((i) => (
                <li key={i.id}>
                  <Row item={i} today={today()} names={names} onToggle={() => toggle(i)} onOpen={() => setOpen(i.id)} />
                </li>
              ))}
            </ul>
          </section>
        ))}
        {groups.size === 0 && (
          <p className="rounded-xl border border-border bg-card px-4 py-12 text-center text-muted-foreground shadow-card">
            {view === "mine" ? "Nothing open for you." : view === "everyone" ? "Nothing open. Add items after your next meeting." : "Nothing ticked off yet."}
          </p>
        )}
      </main>

      {item && (
        <ItemSheet
          key={item.id}
          project={data}
          item={item}
          names={names}
          onChange={put}
          onToggle={() => toggle(item)}
          onRemoved={() => {
            setOpen(null);
            load();
          }}
          onClose={() => setOpen(null)}
        />
      )}
      {adding && (
        <AddSheet
          project={data}
          onAdded={() => {
            setAdding(false);
            setView("everyone");
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
  today: string;
  names: Map<string, string>;
  onToggle: () => void;
  onOpen: () => void;
}

function Row({ item, today, names, onToggle, onOpen }: RowProps) {
  const done = item.status === "done";
  const sub = points(item.detail);
  const meta = "flex items-center gap-1";
  return (
    <div className="flex items-stretch">
      <button aria-label={done ? `Undo ${item.title}` : `Done: ${item.title}`} aria-pressed={done} onClick={onToggle} className="flex w-14 shrink-0 items-center justify-center sm:w-16">
        <Tick done={done} />
      </button>
      <button onClick={onOpen} className="flex min-h-15 min-w-0 flex-1 flex-col justify-center gap-0.5 py-2.5 pr-4 text-left hover:bg-muted/70">
        <span className="text-[16px] leading-snug">
          <span className="strike" data-done={done}>
            {item.title}
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[13px] text-muted-foreground empty:hidden">
          {done && item.done_at && <span className={cx(meta, "text-success")}>{[firstName(names.get(item.done_by ?? "")), when(item.done_at)].filter(Boolean).join(" · ")}</span>}
          {item.tags.map((t) => (
            <span key={t} className="rounded-full bg-muted px-1.5 font-medium">
              {t}
            </span>
          ))}
          {item.due && (
            <span className={cx(meta, !done && item.due < today && "font-medium text-destructive")}>
              <CalendarDays className="size-3.5" />
              {!done && item.due < today ? "Was due" : "Due"} {dayLabel(item.due)}
            </span>
          )}
          {sub > 0 && (
            <span className={meta}>
              <ListTree className="size-3.5" />
              {sub} {sub === 1 ? "point" : "points"}
            </span>
          )}
          {item.notes.length > 0 && (
            <span className={meta}>
              <MessageSquare className="size-3.5" />
              {item.notes.length}
            </span>
          )}
        </span>
      </button>
    </div>
  );
}

interface ItemSheetProps {
  project: Project;
  item: TrackerItem;
  names: Map<string, string>;
  onChange: (item: TrackerItem) => void;
  onToggle: () => void;
  onRemoved: () => void;
  onClose: () => void;
}

function ItemSheet({ project, item, names, onChange, onToggle, onRemoved, onClose }: ItemSheetProps) {
  const { members, admin, tags } = project;
  const [draft, setDraft] = useState<TrackerDraft>({ title: item.title, detail: item.detail, owners: item.owners, tags: item.tags, due: item.due });
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const dirty = (Object.keys(draft) as (keyof TrackerDraft)[]).some((k) => JSON.stringify(draft[k]) !== JSON.stringify(item[k]));
  const done = item.status === "done";
  const mayDelete = admin || item.created_by === project.me;

  const run = async (work: () => Promise<void>, offline: string) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(said(e, offline));
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(async () => onChange(await api.projectUpdate(project.id, item.id, draft)), "No connection. Your changes are not saved yet.");
  const post = () =>
    run(async () => {
      onChange(await api.projectNote(project.id, item.id, note));
      setNote("");
    }, "No connection. Your update was not posted.");
  const remove = () =>
    run(async () => {
      await api.projectRemove(project.id, item.id);
      onRemoved();
    }, "No connection. It was not deleted.");

  return (
    <Sheet title="Action item" onClose={onClose}>
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        <Field label="What needs doing">
          <textarea rows={rowsFor(draft.title, 6)} value={draft.title} onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))} className={cx(inputClass, "py-2.5 leading-snug")} />
        </Field>
        <Field label="Points and details">
          <textarea
            rows={rowsFor(draft.detail, 12)}
            value={draft.detail}
            onChange={(e) => setDraft((d) => ({ ...d, detail: e.target.value }))}
            placeholder="One point per line"
            className={cx(inputClass, "py-2.5 leading-relaxed")}
          />
        </Field>
        <div>
          <p className="mb-1.5 text-sm font-medium text-muted-foreground">Who owns it</p>
          <div className="flex flex-wrap gap-2">
            {members.map((m) => (
              <Chip key={m.id} on={draft.owners.includes(m.id)} onClick={() => setDraft((d) => ({ ...d, owners: flip(d.owners, m.id) }))}>
                <Avatar id={m.id} name={m.name} size="sm" />
                {firstName(m.name)}
              </Chip>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
          {tags.length > 0 && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-muted-foreground">Store</p>
              <div className="flex flex-wrap gap-2">
                {tags.map((t) => (
                  <Chip key={t} on={draft.tags.includes(t)} onClick={() => setDraft((d) => ({ ...d, tags: tags.filter((x) => flip(d.tags, t).includes(x)) }))}>
                    {t}
                  </Chip>
                ))}
              </div>
            </div>
          )}
          <Field label="Due (optional)">
            <Input type="date" value={draft.due ?? ""} onChange={(e) => setDraft((d) => ({ ...d, due: e.target.value || null }))} className="w-44" />
          </Field>
        </div>
        {dirty && (
          <Button className="w-full" disabled={busy || !draft.title.trim()} onClick={save}>
            Save changes
          </Button>
        )}

        <section className="space-y-3 border-t border-border pt-4">
          <h3 className="text-sm font-medium text-muted-foreground">Updates</h3>
          {item.notes.length === 0 && <p className="text-sm text-muted-foreground">No updates yet. Post one as you make progress.</p>}
          <ul className="space-y-3">
            {item.notes.map((n) => (
              <li key={n.id} className="flex gap-3">
                <Avatar id={n.by} name={names.get(n.by) ?? "?"} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-muted-foreground">
                    <span className="font-medium text-foreground">{firstName(names.get(n.by)) || "Someone"}</span> · {when(n.at)}
                  </p>
                  <p className="whitespace-pre-wrap text-[15px] leading-snug">{n.text}</p>
                </div>
              </li>
            ))}
          </ul>
          <div className="flex items-end gap-2">
            <textarea
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              aria-label="Write an update"
              placeholder="Write an update"
              className={cx(inputClass, "flex-1 py-2.5 leading-snug")}
            />
            <Button variant="outline" disabled={busy || !note.trim()} onClick={post}>
              Post
            </Button>
          </div>
        </section>

        <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
          <Button variant={done ? "outline" : "primary"} className="flex-1" onClick={onToggle}>
            {done ? "Reopen" : "Mark done"}
          </Button>
          {mayDelete &&
            (confirming ? (
              <Button variant="danger" disabled={busy} onClick={remove}>
                Yes, delete it
              </Button>
            ) : (
              <Button variant="ghost" aria-label="Delete this item" onClick={() => setConfirming(true)}>
                <Trash2 className="size-4" />
                Delete
              </Button>
            ))}
        </div>
        <p className="text-sm text-muted-foreground">
          Added by {firstName(names.get(item.created_by)) || "someone"} on {when(item.created_at)}.
          {done && item.done_at && ` Done by ${firstName(names.get(item.done_by ?? "")) || "someone"} on ${when(item.done_at)}.`}
        </p>
      </div>
    </Sheet>
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
      if (!items.length) throw new Error("No items found. Put each item on its own line.");
      setDrafts(items);
    });
  const add = () =>
    run(async () => {
      await api.projectAdd(project.id, drafts ?? []);
      track("action", `add ${drafts?.length} action items`);
      onAdded();
    });
  const edit = (n: number, patch: Partial<TrackerDraft>) => setDrafts((d) => d && d.map((x, i) => (i === n ? { ...x, ...patch } : x)));

  if (!drafts) {
    return (
      <Sheet title="Add action items" onClose={onClose}>
        <div className="space-y-4">
          <textarea
            rows={9}
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Items, one per line, or pasted meeting notes"
            placeholder={`One item per line, or paste your meeting notes.\n\n${example}\n1. First item\n2. Second item`}
            className={cx(inputClass, "py-2.5 leading-relaxed")}
          />
          <p className="text-sm text-muted-foreground">A line with only names ({example}) sets who owns the items under it. Without one, the items are yours. Indented lines become points of the item above.</p>
          {error && <Notice tone="error">{error}</Notice>}
          <Button className="min-h-12 w-full text-base" disabled={busy || !text.trim()} onClick={check}>
            {busy ? "Checking…" : "Check the items"}
          </Button>
        </div>
      </Sheet>
    );
  }
  return (
    <Sheet title={`Add ${drafts.length} ${drafts.length === 1 ? "item" : "items"}`} onClose={onClose}>
      <div className="space-y-4">
        <ul className="space-y-3">
          {drafts.map((d, n) => (
            <li key={n} className="space-y-2 rounded-xl border border-border p-3">
              <div className="flex items-start gap-2">
                <textarea rows={rowsFor(d.title, 6)} aria-label="Item" value={d.title} onChange={(e) => edit(n, { title: e.target.value })} className={cx(inputClass, "flex-1 py-2 leading-snug")} />
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
            {busy ? "Adding…" : `Add ${drafts.length} ${drafts.length === 1 ? "item" : "items"}`}
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
