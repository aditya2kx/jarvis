import { ChevronLeft, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, shopDevice, type Bootstrap, type Account, type HistoryDay, type Session, type Staff, type Template } from "./api";
import { repeatLabel } from "./Repeat";
import { blankDraft, TaskForm, type TaskDraft } from "./TaskForm";
import { addDays, Button, cx, dayLabel, Field, Input, inputClass, Notice, Sheet } from "./ui";

const TABS = ["Tasks", "People", "Sign-in", "History"] as const;
type Tab = (typeof TABS)[number];
const HISTORY_DAYS = 14;

interface Props {
  boot: Bootstrap;
  session: Session;
  onBack: () => void;
}

export function Manage({ boot, session, onBack }: Props) {
  const [tab, setTab] = useState<Tab>("Tasks");
  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col">
      <header className="space-y-3 px-4 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-6">
        <button onClick={onBack} className="-ml-2 flex items-center gap-1 rounded-lg p-2 text-muted-foreground active:bg-muted">
          <ChevronLeft className="size-5" />
          Back to today
        </button>
        <div role="tablist" className="grid grid-cols-4 gap-1 rounded-xl bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={t === tab}
              onClick={() => setTab(t)}
              className={cx("min-h-10 rounded-lg text-[15px] font-medium", t === tab ? "bg-card shadow-sm" : "text-muted-foreground")}
            >
              {t}
            </button>
          ))}
        </div>
      </header>
      <main className="flex-1 overflow-y-auto px-4 pb-10 pt-4 sm:px-6">
        {tab === "Tasks" && <Templates boot={boot} session={session} />}
        {tab === "People" && <People token={session.token} />}
        {tab === "Sign-in" && (
          <div className="space-y-8">
            <ShopCodeCard token={session.token} />
            <Accounts token={session.token} mine={boot.account.email} />
          </div>
        )}
        {tab === "History" && <History boot={boot} token={session.token} />}
      </main>
    </div>
  );
}

function useLoad<T>(load: () => Promise<T>): [T | null, string, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const run = useCallback(() => {
    load()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e: Error) => setError(e.message || "Could not load. Check the connection."));
  }, [load]);
  useEffect(run, [run]);
  return [data, error, run];
}

function Card({ children }: { children: React.ReactNode }) {
  return <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">{children}</ul>;
}

function RowButton({ onClick, title, meta, dim }: { onClick: () => void; title: string; meta?: string; dim?: boolean }) {
  return (
    <li>
      <button onClick={onClick} className={cx("flex min-h-14 w-full items-center justify-between gap-4 px-4 py-2 text-left active:bg-muted", dim && "opacity-50")}>
        <span className="text-[16px]">{title}</span>
        {meta && <span className="shrink-0 text-sm text-muted-foreground">{meta}</span>}
      </button>
    </li>
  );
}

function Templates({ boot, session }: { boot: Bootstrap; session: Session }) {
  const token = session.token;
  const load = useCallback(() => api.templates(token).then((r) => r.templates), [token]);
  const [templates, error, reload] = useLoad(load);
  const [editing, setEditing] = useState<TaskDraft | null>(null);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!templates) return <p className="text-muted-foreground">Loading…</p>;
  const when = (t: Template) => [repeatLabel(t, boot.today), t.needs_photo ? "Photo needed" : ""].filter(Boolean).join(" · ");

  return (
    <div className="space-y-6">
      <Notice>Tasks that repeat. Changes show from today on tasks not done yet; earlier versions are kept. To copy a task, open it on a day's list and choose Duplicate.</Notice>
      {boot.store.shifts.map((s) => {
        const mine = templates.filter((t) => t.shift === s.id);
        return (
          <section key={s.id}>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-semibold">{s.label} <span className="font-normal text-muted-foreground">· {mine.filter((t) => t.active).length}</span></h2>
              <Button variant="ghost" onClick={() => setEditing(blankDraft(s.id))}>
                <Plus className="size-4" />
                Add
              </Button>
            </div>
            <Card>
              {mine.filter((t) => !t.parent_id).map((t) => (
                <div key={t.id}>
                  <RowButton onClick={() => setEditing(t)} title={t.title} meta={when(t)} dim={!t.active} />
                  {mine.filter((c) => c.parent_id === t.id).map((c) => (
                    <div key={c.id} className="border-t border-border bg-muted/40 pl-6">
                      <RowButton onClick={() => setEditing(c)} title={c.title} meta={when(c)} dim={!c.active} />
                    </div>
                  ))}
                </div>
              ))}
            </Card>
          </section>
        );
      })}
      {editing && (
        <TaskForm
          boot={boot}
          session={session}
          date={boot.today}
          title={editing.id ? "Edit task" : "New repeating task"}
          initial={editing}
          once={false}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function People({ token }: { token: string }) {
  const load = useCallback(() => api.staff(token).then((r) => r.staff), [token]);
  const [staff, error, reload] = useLoad(load);
  const [editing, setEditing] = useState<Partial<Staff> | null>(null);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!staff) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="outline" onClick={() => setEditing({ role: "crew", active: true })}>
          <Plus className="size-4" />
          Add person
        </Button>
      </div>
      <Card>
        {staff.map((s) => (
          <RowButton
            key={s.id}
            onClick={() => setEditing(s)}
            title={s.name}
            meta={[s.role === "manager" ? "Manager" : "Crew", s.active ? (s.has_pin ? "" : "No PIN yet") : "Inactive"].filter(Boolean).join(" · ")}
            dim={!s.active}
          />
        ))}
      </Card>
      {editing && (
        <PersonForm
          token={token}
          value={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function PersonForm({ token, value, onClose, onSaved }: { token: string; value: Partial<Staff>; onClose: () => void; onSaved: () => void }) {
  const [s, setS] = useState(value);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const pinLength = s.role === "manager" ? 6 : 4;
  const roleChanged = Boolean(value.id) && s.role !== value.role;
  const pinOk = pin.length === 0 || pin.length === pinLength;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const saved = (await api.saveStaff(token, s)).staff;
      if (pin) await api.setPin(token, saved.id, pin);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Sheet title={value.id ? "Edit person" : "Add person"} onClose={onClose}>
      <form onSubmit={save} className="space-y-4">
        <Field label="Name">
          <Input autoFocus={!value.id} value={s.name ?? ""} onChange={(e) => setS({ ...s, name: e.target.value })} />
        </Field>
        <Field label="Name on the ADP schedule (if different)">
          <Input placeholder="Last, First" value={s.adp_name ?? ""} onChange={(e) => setS({ ...s, adp_name: e.target.value })} />
        </Field>
        <Field label="Role">
          <select value={s.role} onChange={(e) => setS({ ...s, role: e.target.value as Staff["role"] })} className={inputClass}>
            <option value="crew">Crew</option>
            <option value="manager">Manager</option>
          </select>
        </Field>
        <Field label={`${value.has_pin && !roleChanged ? "New PIN (leave empty to keep)" : "PIN"} · ${pinLength} digits`}>
          <Input inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, pinLength))} />
        </Field>
        {roleChanged && <Notice>Changing the role clears the PIN. Set a new one here.</Notice>}
        <label className="flex min-h-11 items-center gap-3">
          <input type="checkbox" checked={s.active !== false} onChange={(e) => setS({ ...s, active: e.target.checked })} className="size-5 accent-primary" />
          Can sign in
        </label>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" disabled={!s.name?.trim() || !pinOk} className="w-full">Save</Button>
      </form>
    </Sheet>
  );
}

function ShopCodeCard({ token }: { token: string }) {
  const load = useCallback(() => api.shopCode(token), [token]);
  const [data, error, reload] = useLoad(load);
  const [code, setCode] = useState("");
  const [failed, setFailed] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <p className="text-muted-foreground">Loading…</p>;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFailed("");
    try {
      const r = await api.setShopCode(token, code);
      if (r.device) shopDevice.set(r.device);
      setCode("");
      setSaved(true);
      reload();
    } catch (err) {
      setFailed((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Shop device</h2>
      <p className="text-muted-foreground">
        The shop iPad needs no Google account. Open {data.url ? <span className="font-medium text-foreground break-all">{data.url}</span> : "the shop address"} on it and enter
        this 6-digit code once; it stays signed in as the shop. Crew pick who is doing each task, and a manager signs in with their PIN to manage.
      </p>
      <form onSubmit={save} className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:flex-row sm:items-end">
        <Field label={data.set ? "New shop code" : "Shop code"}>
          <Input
            inputMode="numeric"
            autoComplete="off"
            maxLength={6}
            placeholder="6 digits"
            value={code}
            onChange={(e) => {
              setSaved(false);
              setCode(e.target.value.replace(/\D/g, ""));
            }}
          />
        </Field>
        <Button type="submit" disabled={code.length !== 6 || busy} className="sm:w-48">
          {busy ? "Saving…" : data.set ? "Change code" : "Set code"}
        </Button>
      </form>
      {failed && <Notice tone="error">{failed}</Notice>}
      {saved && <Notice>Saved. Shop devices that used the old code enter the new one.</Notice>}
      {data.set && !saved && data.updated_at && (
        <p className="text-sm text-muted-foreground">Set {new Date(data.updated_at).toLocaleDateString()}. Changing it signs every shop device out.</p>
      )}
    </section>
  );
}

function Accounts({ token, mine }: { token: string; mine: string }) {
  const load = useCallback(() => Promise.all([api.accounts(token), api.staff(token)]).then(([a, s]) => ({ accounts: a.accounts, staff: s.staff })), [token]);
  const [data, error, reload] = useLoad(load);
  const [email, setEmail] = useState("");
  const [staffId, setStaffId] = useState("");
  const [label, setLabel] = useState("");
  const [removing, setRemoving] = useState<Account | null>(null);
  const [failed, setFailed] = useState("");
  const [busy, setBusy] = useState(false);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  const names = new Map(data.staff.map((s) => [s.id, s.name]));
  const managers = new Set(data.staff.filter((s) => s.role === "manager").map((s) => s.id));

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFailed("");
    try {
      await api.saveAccount(token, { email, staff_id: staffId || null, label: staffId ? "" : label });
      setEmail("");
      setLabel("");
      setStaffId("");
      reload();
    } catch (err) {
      setFailed((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Notice>Only these Google accounts can open Store Tasks. A shared account (a store iPad) asks each person for their name and PIN; a person's own account signs them straight in. To make a shop manager, set their role to Manager under People, then give their Google account access here — they can then add and change tasks.</Notice>
      <form onSubmit={add} className="space-y-3 rounded-xl border border-border bg-card p-4">
        <Field label="Google account">
          <Input type="email" inputMode="email" autoCapitalize="off" placeholder="name@gmail.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Belongs to">
            <select value={staffId} onChange={(e) => setStaffId(e.target.value)} className={inputClass}>
              <option value="">Shared (store iPad)</option>
              {data.staff.filter((s) => s.active).map((s) => (
                <option key={s.id} value={s.id}>{s.role === "manager" ? `${s.name} · Manager` : s.name}</option>
              ))}
            </select>
          </Field>
          {!staffId && (
            <Field label="Name for this device">
              <Input placeholder="Front iPad" value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
          )}
        </div>
        {failed && <Notice tone="error">{failed}</Notice>}
        <Button type="submit" disabled={!email.includes("@") || busy} className="w-full">{busy ? "Adding…" : "Give access"}</Button>
      </form>
      <Card>
        {data.accounts.map((a) => (
          <RowButton
            key={a.email}
            onClick={() => a.email !== mine && setRemoving(a)}
            title={a.email}
            meta={[a.staff_id ? names.get(a.staff_id) ?? "Person" : a.label || "Shared", a.staff_id && managers.has(a.staff_id) ? "Manager" : "", a.email === mine ? "this one" : ""].filter(Boolean).join(" · ")}
          />
        ))}
      </Card>
      {removing && (
        <Sheet title={`Remove ${removing.email}?`} onClose={() => setRemoving(null)}>
          <p className="mb-4 text-muted-foreground">It loses access straight away.</p>
          <Button
            variant="danger"
            className="w-full"
            onClick={async () => {
              await api.removeAccount(token, removing.email).catch((e: Error) => setFailed(e.message));
              setRemoving(null);
              reload();
            }}
          >
            Remove access
          </Button>
        </Sheet>
      )}
    </div>
  );
}

function History({ boot, token }: { boot: Bootstrap; token: string }) {
  const load = useCallback(() => api.history(token, addDays(boot.today, -HISTORY_DAYS + 1), boot.today).then((r) => r.days), [token, boot.today]);
  const [days, error] = useLoad(load);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!days) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="w-full text-left text-[15px] tabular-nums">
        <thead className="text-sm text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-medium">Day</th>
            {boot.store.shifts.map((s) => (
              <th key={s.id} className="px-4 py-3 font-medium">{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {[...days].reverse().map((d: HistoryDay) => (
            <tr key={d.date}>
              <td className="px-4 py-3">{dayLabel(d.date)}</td>
              {boot.store.shifts.map((s) => {
                const c = d.shifts[s.id] ?? { total: 0, done: 0, late: 0 };
                return (
                  <td key={s.id} className={cx("px-4 py-3", c.total > 0 && c.done < c.total && "text-destructive")}>
                    {c.total ? `${c.done}/${c.total}` : "—"}
                    {c.late > 0 && <span className="text-muted-foreground"> · {c.late} late</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
