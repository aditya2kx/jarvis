import { Check, ChevronDown, ChevronRight, LayoutGrid, ListTodo, Store } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./api";
import { cx, Sheet } from "./ui";

interface Place {
  key: string;
  name: string;
  hint: string;
  href: string;
  Icon: typeof Store;
}

let places: Promise<Place[]> | null = null;

/** Everywhere this Google account can go. A shop device or an account with no projects gets none. */
function loadPlaces(): Promise<Place[]> {
  places ??= api
    .projects()
    .then(({ store, projects }) => [
      ...(store ? [{ key: "store", name: `${store.name} tasks`, hint: "Daily opening, shift change and closing lists", href: "/", Icon: Store }] : []),
      ...projects.map((p) => ({ key: p.id, name: p.name, hint: "Private project", href: `/p/${p.id}`, Icon: ListTodo })),
    ])
    .catch(() => {
      places = null;
      return [];
    });
  return places;
}

/** Switch between the store's tasks and the private projects; shown only to someone on more than one.
 * `inline`: on a phone, where the header has no room for another button, the given name (the store's)
 * is the switch instead; wider screens get the Projects button and the name stays plain text. */
export function ProjectSwitch({ current, inline }: { current: string; inline?: string }) {
  const [list, setList] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    loadPlaces().then((p) => live && setList(p));
    return () => {
      live = false;
    };
  }, []);
  if (list.length < 2) return inline ?? null;
  return (
    <>
      {inline !== undefined ? (
        <>
          <button onClick={() => setOpen(true)} aria-label={`${inline}: switch project`} className="inline-flex items-center gap-0.5 font-medium text-foreground sm:hidden">
            {inline}
            <ChevronDown className="size-3.5" />
          </button>
          <span className="hidden sm:inline">{inline}</span>
        </>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="flex min-h-10 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-[15px] font-medium transition hover:bg-muted active:scale-[0.97]"
        >
          <LayoutGrid className="size-4" />
          Projects
        </button>
      )}
      {open && (
        <Sheet title="Your projects" onClose={() => setOpen(false)}>
          <ul className="space-y-2">
            {list.map((p) => {
              const here = p.key === current;
              return (
                <li key={p.key}>
                  <a
                    href={p.href}
                    aria-current={here ? "page" : undefined}
                    onClick={(e) => {
                      if (here) {
                        e.preventDefault();
                        setOpen(false);
                      }
                    }}
                    className={cx(
                      "flex min-h-16 items-center gap-3 rounded-xl border px-4 py-2 transition hover:bg-muted active:scale-[0.99]",
                      here ? "border-primary bg-accent" : "border-border bg-card",
                    )}
                  >
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-muted text-primary">
                      <p.Icon className="size-5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[16px] font-medium">{p.name}</span>
                      <span className="block truncate text-sm text-muted-foreground">{here ? "You are here" : p.hint}</span>
                    </span>
                    {here ? <Check className="size-5 text-primary" /> : <ChevronRight className="size-5 text-muted-foreground" />}
                  </a>
                </li>
              );
            })}
          </ul>
        </Sheet>
      )}
    </>
  );
}
