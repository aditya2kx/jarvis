import { Check, UserX } from "lucide-react";
import { useState } from "react";
import { Avatar, Button, cx, Input, Sheet } from "./ui";

interface Pickable {
  id: string;
  name: string;
  /** Store staff only: off when they are not active in ADP. */
  assignable?: boolean;
}

interface Props {
  title: string;
  staff: Pickable[];
  /** People on today's schedule, listed first. Everyone can still be picked. */
  scheduled?: Set<string>;
  me: string;
  current?: string | null;
  onPick: (staffId: string | null) => void;
  onClose: () => void;
  /** Under the list; a store says where its people come from. */
  footnote?: string | null;
  /** Pick several people (a project task can have more than one owner); saved with one tap. */
  many?: { picked: string[]; onSave: (ids: string[]) => void };
}

const ADP = "Everyone active in ADP. Someone missing? They need to be active there.";

/** Pick who is doing a task: anyone on the team, or no one. */
export function PersonPicker({ title, staff, scheduled = new Set(), me, current, onPick, onClose, footnote = ADP, many }: Props) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string[]>(many?.picked ?? []);
  const people = staff.filter((s) => s.assignable !== false);
  const groups = (() => {
    const match = people.filter((s) => s.name.toLowerCase().includes(query.trim().toLowerCase()));
    const mine = match.filter((s) => s.id === me);
    const today = match.filter((s) => s.id !== me && scheduled.has(s.id));
    const rest = match.filter((s) => s.id !== me && !scheduled.has(s.id));
    return [
      { label: "", people: mine },
      { label: "Scheduled today", people: today },
      { label: today.length ? "Everyone else" : "Team", people: rest },
    ].filter((g) => g.people.length);
  })();

  const isPicked = (id: string) => (many ? picked.includes(id) : id === current);
  const pick = (id: string | null) => {
    if (!many) return onPick(id);
    setPicked((p) => (id === null ? [] : p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };

  const row = (key: string, selected: boolean, onClick: () => void, icon: React.ReactNode, label: string, hint?: string) => (
    <li key={key}>
      <button
        onClick={onClick}
        aria-pressed={many ? selected : undefined}
        className={cx("flex min-h-13 w-full items-center gap-3 rounded-lg px-2 text-left hover:bg-muted active:bg-muted", selected && "bg-muted")}
      >
        {icon}
        <span className="min-w-0 flex-1 truncate text-[16px]">
          {label}
          {hint && <span className="ml-2 text-sm text-muted-foreground">{hint}</span>}
        </span>
        {selected && <Check className="size-5 shrink-0" />}
      </button>
    </li>
  );

  return (
    <Sheet title={title} onClose={onClose}>
      <div className="space-y-3">
        {people.length > 8 && <Input placeholder="Search people" value={query} onChange={(e) => setQuery(e.target.value)} />}
        {groups.map((g) => (
          <section key={g.label}>
            {g.label && <h3 className="px-2 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.label}</h3>}
            <ul>{g.people.map((s) => row(s.id, isPicked(s.id), () => pick(s.id), <Avatar id={s.id} name={s.name} />, s.name, s.id === me ? "Me" : undefined))}</ul>
          </section>
        ))}
        {!groups.length && <p className="px-2 py-6 text-center text-muted-foreground">{query ? "No one by that name." : "No one is active in ADP."}</p>}
        {footnote && <p className="px-2 text-sm text-muted-foreground">{footnote}</p>}
        <ul className="border-t border-border pt-2">
          {row(
            "none",
            many ? picked.length === 0 : current === null,
            () => pick(null),
            <span className="flex size-8 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <UserX className="size-4" />
            </span>,
            "No one",
          )}
        </ul>
        {many && (
          <Button className="min-h-12 w-full text-base" onClick={() => many.onSave(picked)}>
            Save
          </Button>
        )}
      </div>
    </Sheet>
  );
}
