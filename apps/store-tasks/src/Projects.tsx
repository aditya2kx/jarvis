import { Check, ChevronsUpDown, ListTodo, Store, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { cx, Sheet, useEscape, useWide } from "./ui";

interface Place {
  key: string;
  name: string;
  hint: string;
  href: string;
  Icon: LucideIcon;
}

let places: Promise<Place[]> | null = null;

export const storeKey = (id: string) => `store:${id}`;

/** Everywhere this Google account can go. A shop device or an account with no projects gets none. */
function loadPlaces(): Promise<Place[]> {
  places ??= api
    .projects()
    .then(({ store, stores, projects }) => [
      ...(stores ?? (store ? [store] : [])).map((s) => ({
        key: storeKey(s.id),
        name: `${s.name} tasks`,
        hint: "Opening, shift change and closing lists",
        href: `/?store=${encodeURIComponent(s.id)}`,
        Icon: Store,
      })),
      ...projects.map((p) => ({ key: p.id, name: p.name, hint: "Private project", href: `/p/${p.id}`, Icon: ListTodo })),
    ])
    .catch(() => {
      places = null;
      return [];
    });
  return places;
}

/** The places this person can switch between; fewer than two means there is nothing to switch. */
export function usePlaces(enabled = true): Place[] {
  const [list, setList] = useState<Place[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    loadPlaces().then((p) => live && setList(p));
    return () => {
      live = false;
    };
  }, [enabled]);
  return list;
}

interface Props {
  places: Place[];
  current: string;
  /** Shown beside the tile; `compact` leaves only the tile on a phone, where the header has no room. */
  label: string;
  compact?: boolean;
  big?: boolean;
}

/** The workspace switcher, top left on every screen as in Slack or Notion: a tile, the name, and a
 * menu of every place this person can open. A menu under it on a wide screen, a sheet on a phone. */
export function ProjectSwitch({ places, current, label, compact, big }: Props) {
  const [open, setOpen] = useState(false);
  const wide = useWide("(min-width: 640px)");
  const Icon = places.find((p) => p.key === current)?.Icon ?? Store;

  const list = (
    <ul className="space-y-1">
      {places.map((p) => {
        const on = p.key === current;
        return (
          <li key={p.key}>
            <a
              href={p.href}
              aria-current={on ? "page" : undefined}
              onClick={(e) => {
                if (on) {
                  e.preventDefault();
                  setOpen(false);
                }
              }}
              className={cx("flex min-h-14 items-center gap-3 rounded-lg px-2 py-1.5 transition hover:bg-muted active:bg-muted", on && "bg-accent")}
            >
              <Tile Icon={p.Icon} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px] font-medium">{p.name}</span>
                <span className="block truncate text-[13px] text-muted-foreground">{p.hint}</span>
              </span>
              {on && <Check className="size-5 shrink-0 text-primary" />}
            </a>
          </li>
        );
      })}
    </ul>
  );

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${label}: switch project`}
        className={cx(
          "-ml-1 flex min-h-11 items-center gap-2 rounded-xl py-1 pl-1 text-left transition hover:bg-muted active:scale-[0.98]",
          compact ? "pr-1 sm:pr-1.5" : "pr-1.5",
          open && "bg-muted",
        )}
      >
        <Tile Icon={Icon} />
        <span className={cx("truncate font-extrabold tracking-tight", big ? "text-2xl" : "text-[15px]", compact && "hidden sm:inline")}>{label}</span>
        <ChevronsUpDown className={cx("size-4 shrink-0 text-muted-foreground", compact && "hidden sm:block")} />
      </button>
      {open &&
        (wide ? (
          <Menu onClose={() => setOpen(false)}>{list}</Menu>
        ) : (
          <Sheet title="Switch project" onClose={() => setOpen(false)}>
            {list}
          </Sheet>
        ))}
    </div>
  );
}

function Tile({ Icon }: { Icon: LucideIcon }) {
  return (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-card">
      <Icon className="size-5" />
    </span>
  );
}

function Menu({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEscape(onClose);
  useEffect(() => {
    // A tap anywhere but the menu or its button closes it; the button's own tap toggles it.
    const away = (e: PointerEvent) => {
      if (!ref.current?.parentElement?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [onClose]);
  return (
    <div ref={ref} role="menu" className="pop absolute left-0 top-full z-40 mt-2 w-80 rounded-xl border border-border bg-card p-2 shadow-float">
      <p className="px-2 pb-1.5 pt-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Switch project</p>
      {children}
    </div>
  );
}
