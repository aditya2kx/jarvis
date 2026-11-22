import { Check, MousePointerClick, Plus, Search, Send, UserPlus, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Avatar, Button, cx, firstName, inputClass, Sheet, Tick, time } from "./ui";

/** The pieces every list in the app is built from (a store's shifts, a project), so they look and work alike. */

export interface Person {
  id: string;
  name: string;
}

export interface NoteLike {
  id: string;
  by: string;
  at: string;
  text: string;
  reply_to?: string | null;
}

interface RowProps {
  title: string;
  done: boolean;
  /** Ticked here but not sent yet. */
  pending?: boolean;
  sub?: boolean;
  selected: boolean;
  readOnly?: boolean;
  /** The small grey line under the title (who did it, steps, photos, notes…). */
  meta: ReactNode;
  /** Who is doing it: one person on a shift, one or more on a project. */
  people: Person[];
  /** The show/hide steps button, for a task with steps. */
  expander?: ReactNode;
  onToggle: () => void;
  onOpen: () => void;
  onAssign: () => void;
}

/** One task in a list: the tick, the title and its details, and who is doing it. */
export function TaskRow({ title, done, pending, sub, selected, readOnly, meta, people, expander, onToggle, onOpen, onAssign }: RowProps) {
  const who = people.map((p) => firstName(p.name)).join(" + ");
  return (
    <div className={cx("flex items-stretch transition-colors", sub && "pl-6 sm:pl-8", selected && "bg-tone-soft shadow-[inset_3px_0_0_var(--tone)]")}>
      <button
        aria-label={done ? `Undo ${title}` : `Done: ${title}`}
        aria-pressed={done}
        disabled={readOnly}
        onClick={onToggle}
        className="flex w-14 shrink-0 items-center justify-center disabled:opacity-60 sm:w-16"
      >
        <Tick done={done} dim={pending} />
      </button>
      <button onClick={onOpen} className={cx("flex min-h-15 min-w-0 flex-1 flex-col justify-center gap-0.5 py-2.5 pr-2 text-left", !selected && "hover:bg-muted/70")}>
        <span className="text-[16px] leading-snug">
          <span className="strike" data-done={done}>
            {title}
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[13px] text-muted-foreground empty:hidden">{meta}</span>
      </button>
      {expander}
      <button
        disabled={readOnly}
        onClick={onAssign}
        aria-label={who ? `Doing it: ${who}. Change` : "Pick who is doing it"}
        className="group flex shrink-0 items-center gap-2 px-3 sm:px-4"
      >
        {people.length ? (
          <>
            <span className="hidden max-w-36 truncate text-sm text-muted-foreground md:inline">{who}</span>
            <span key={people.map((p) => p.id).join()} className="pop inline-flex -space-x-2 transition-transform group-enabled:group-hover:scale-110">
              {people.map((p) => (
                <span key={p.id} className="rounded-full ring-2 ring-card">
                  <Avatar id={p.id} name={p.name} />
                </span>
              ))}
            </span>
          </>
        ) : (
          <span
            className={cx(
              "flex size-8 items-center justify-center rounded-full border border-dashed border-ring text-muted-foreground transition group-enabled:group-hover:border-primary group-enabled:group-hover:bg-accent group-enabled:group-hover:text-primary",
              readOnly && "opacity-0",
            )}
          >
            <UserPlus className="size-4" />
          </span>
        )}
      </button>
    </div>
  );
}

/** Beside the list on a wide screen (iPad landscape, desktop); a sheet over it on anything narrower. */
export function DetailPane({ wide, title, empty, onClose, children }: { wide: boolean; title: string | null; empty: string; onClose: () => void; children: ReactNode }) {
  if (!wide) {
    return title !== null ? (
      <Sheet title={title} onClose={onClose}>
        {children}
      </Sheet>
    ) : null;
  }
  return (
    <aside className="w-[26rem] shrink-0 overflow-y-auto border-l border-border bg-card xl:w-[30rem]">
      {title !== null ? (
        <div key={title} className="slide-in p-6">
          <div className="mb-4 flex items-start justify-between gap-3">
            <h2 className="text-xl font-semibold leading-snug">{title}</h2>
            <button aria-label="Close" onClick={onClose} className="-m-2 rounded-full p-2 text-muted-foreground hover:bg-muted">
              <X className="size-5" />
            </button>
          </div>
          {children}
        </div>
      ) : (
        <div className="fade flex h-full flex-col items-center justify-center gap-3 p-10 text-center text-muted-foreground">
          <span className="flex size-14 items-center justify-center rounded-full bg-tone-soft text-tone-ink">
            <MousePointerClick className="size-6" />
          </span>
          <p>{empty}</p>
        </div>
      )}
    </aside>
  );
}

export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex min-h-7 items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Done (by whom, when, and undo) or the big Mark done button. */
export function DoneBar({ done, said, readOnly, blocked, onToggle }: { done: boolean; said: string; readOnly: boolean; blocked?: boolean; onToggle: () => void }) {
  if (done) {
    return (
      <div className="pop flex items-center justify-between gap-3 rounded-xl bg-success/10 px-4 py-3">
        <p className="flex items-center gap-2 font-medium text-success">
          <Check className="size-5" strokeWidth={3} />
          <span>{said}</span>
        </p>
        {!readOnly && (
          <Button variant="outline" onClick={onToggle}>
            Undo
          </Button>
        )}
      </div>
    );
  }
  return readOnly ? null : (
    <Button disabled={blocked} onClick={onToggle} className="min-h-13 w-full text-base">
      <Check className="size-5" />
      Mark done
    </Button>
  );
}

/** "Who is doing it": the people, and a tap to change them. */
export function WhoButton({ people, readOnly, onClick }: { people: Person[]; readOnly: boolean; onClick: () => void }) {
  return (
    <button
      disabled={readOnly}
      onClick={onClick}
      className="flex min-h-12 min-w-0 flex-1 items-center gap-3 rounded-lg border border-border bg-card px-3 text-left transition enabled:hover:border-ring enabled:hover:bg-accent"
    >
      {people.length ? (
        <span key={people.map((p) => p.id).join()} className="pop inline-flex -space-x-2">
          {people.map((p) => (
            <span key={p.id} className="rounded-full ring-2 ring-card">
              <Avatar id={p.id} name={p.name} />
            </span>
          ))}
        </span>
      ) : (
        <span className="flex size-8 items-center justify-center rounded-full border border-dashed border-ring text-muted-foreground">
          <UserPlus className="size-4" />
        </span>
      )}
      <span className={cx("min-w-0 flex-1 truncate text-[16px]", !people.length && "text-muted-foreground")}>
        {people.length ? people.map((p) => (people.length > 1 ? firstName(p.name) : p.name)).join(" + ") : readOnly ? "No one" : "Pick a person"}
      </span>
      {!readOnly && people.length > 0 && <span className="text-sm text-muted-foreground">Change</span>}
    </button>
  );
}

/** A task's notes, oldest first, and the box to add one. `onSend` throws to keep the text. */
export function Notes({ notes, names, readOnly, when = time, onSend }: {
  notes: NoteLike[];
  names: Map<string, string>;
  readOnly: boolean;
  when?: (iso: string) => string;
  onSend: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const send = async () => {
    if (!text.trim()) return;
    await onSend(text.trim());
    setText("");
  };
  return (
    <Section title={notes.length ? `Notes · ${notes.length}` : "Notes"}>
      <div className="space-y-3">
        {notes.length > 0 && (
          <ul className="space-y-3">
            {notes.map((c) => (
              <li key={c.id} className={cx("flex gap-3", c.reply_to && "pl-11")}>
                <Avatar id={c.by} name={names.get(c.by) ?? "?"} />
                <div className="rise min-w-0 flex-1 rounded-lg bg-muted px-3 py-2">
                  <p className="text-sm text-muted-foreground">
                    {firstName(names.get(c.by)) || "Someone"} · {when(c.at)}
                  </p>
                  <p className="whitespace-pre-wrap break-words">{c.text}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
        {!notes.length && readOnly && <p className="text-muted-foreground">No notes.</p>}
        {!readOnly && (
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              send().catch(() => undefined);
            }}
          >
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a note for the team…" className={inputClass} />
            <Button type="submit" aria-label="Send note" disabled={!text.trim()} className="shrink-0 px-3">
              <Send className="size-4" />
            </Button>
          </form>
        )}
      </div>
    </Section>
  );
}

/** "Find a task": matches every word typed, anywhere in the task. */
export function SearchBox({ query, onChange, onDone }: { query: string; onChange: (q: string) => void; onDone?: () => void }) {
  return (
    <div className="relative mb-2">
      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" />
      <input
        type="search"
        value={query}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onDone}
        placeholder="Find a task"
        aria-label="Find a task"
        enterKeyHint="search"
        className={cx(inputClass, "pl-10 pr-11 [&::-webkit-search-cancel-button]:hidden")}
      />
      {query && (
        <button aria-label="Clear search" onClick={() => onChange("")} className="absolute right-1 top-1/2 flex size-10 -translate-y-1/2 items-center justify-center rounded-full text-muted-foreground hover:bg-muted">
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

export const searchWords = (query: string) => query.trim().toLowerCase().split(/\s+/).filter(Boolean);
export const matchesWords = (words: string[], ...text: (string | null | undefined)[]) => {
  const hay = text.join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
};

/** All / Mine / Unassigned with counts, and Hide done. `children` go beside Hide done. */
export function FilterBar<F extends string>({ options, value, onChange, hideDone, onHideDone, children }: {
  options: { id: F; label: string; n: number }[];
  value: F;
  onChange: (f: F) => void;
  hideDone: boolean;
  onHideDone: (on: boolean) => void;
  children?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div role="group" aria-label="Show" className="flex gap-1 rounded-lg bg-muted p-1">
        {options.map((f) => (
          <button
            key={f.id}
            aria-pressed={value === f.id}
            onClick={() => onChange(f.id)}
            className={cx("min-h-9 rounded-md px-3 text-sm font-medium transition", value === f.id ? "bg-card text-foreground shadow-card" : "text-muted-foreground hover:text-foreground")}
          >
            {f.label} <span className="tabular-nums opacity-60">{f.n}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <label className="flex min-h-9 items-center gap-2 text-sm text-muted-foreground">
          <input type="checkbox" checked={hideDone} onChange={(e) => onHideDone(e.target.checked)} className="size-4 accent-primary" />
          Hide done
        </label>
        {children}
      </div>
    </div>
  );
}

/** The last row of a list. */
export function AddRow({ onClick }: { onClick: () => void }) {
  return (
    <li>
      <button onClick={onClick} className="group flex min-h-13 w-full items-center gap-3 px-4 text-left font-medium text-primary hover:bg-accent">
        <Plus className="size-5 transition-transform group-hover:rotate-90" />
        Add a task
      </button>
    </li>
  );
}
