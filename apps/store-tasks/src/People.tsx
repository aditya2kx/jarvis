import { Check, UserX } from "lucide-react";
import { useMemo, useState } from "react";
import type { Staff } from "./api";
import { Avatar, cx, Input, Sheet } from "./ui";

interface Props {
  title: string;
  staff: Staff[];
  /** People on today's schedule, listed first. Everyone can still be picked. */
  scheduled: Set<string>;
  me: string;
  current?: string | null;
  onPick: (staffId: string | null) => void;
  onClose: () => void;
}

/** Pick who is doing a task: anyone on the team, or no one. */
export function PersonPicker({ title, staff, scheduled, me, current, onPick, onClose }: Props) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const match = staff.filter((s) => s.assignable && s.name.toLowerCase().includes(query.trim().toLowerCase()));
    const mine = match.filter((s) => s.id === me);
    const today = match.filter((s) => s.id !== me && scheduled.has(s.id));
    const rest = match.filter((s) => s.id !== me && !scheduled.has(s.id));
    return [
      { label: "", people: mine },
      { label: "Scheduled today", people: today },
      { label: today.length ? "Everyone else" : "Team", people: rest },
    ].filter((g) => g.people.length);
  }, [staff, scheduled, me, query]);

  const row = (key: string, selected: boolean, onClick: () => void, icon: React.ReactNode, label: string, hint?: string) => (
    <li key={key}>
      <button onClick={onClick} className={cx("flex min-h-13 w-full items-center gap-3 rounded-lg px-2 text-left hover:bg-muted active:bg-muted", selected && "bg-muted")}>
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
        {staff.filter((s) => s.assignable).length > 8 && <Input placeholder="Search people" value={query} onChange={(e) => setQuery(e.target.value)} />}
        {groups.map((g) => (
          <section key={g.label}>
            {g.label && <h3 className="px-2 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.label}</h3>}
            <ul>{g.people.map((s) => row(s.id, s.id === current, () => onPick(s.id), <Avatar id={s.id} name={s.name} />, s.name, s.id === me ? "Me" : undefined))}</ul>
          </section>
        ))}
        {!groups.length && <p className="px-2 py-6 text-center text-muted-foreground">{query ? "No one by that name." : "No one is active in ADP."}</p>}
        <p className="px-2 text-sm text-muted-foreground">Everyone active in ADP. Someone missing? They need to be active there.</p>
        <ul className="border-t border-border pt-2">
          {row(
            "none",
            current === null,
            () => onPick(null),
            <span className="flex size-8 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <UserX className="size-4" />
            </span>,
            "No one",
          )}
        </ul>
      </div>
    </Sheet>
  );
}
