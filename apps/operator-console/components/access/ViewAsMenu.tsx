"use client";

import { useMemo, useState, type ReactElement } from "react";
import { Popover } from "@base-ui/react/popover";
import { CheckIcon, EyeIcon, SearchIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PersonAvatar, RoleBadge } from "./visuals";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { AccessDirectory, AccessPerson } from "@/lib/access/people";
import { cn } from "@/lib/utils";

/** Top-bar entry for owners: View as… from any page. */
export function ViewAsButton() {
  return (
    <ViewAsMenu
      current={null}
      trigger={
        <Button variant="outline" size="sm" className="gap-1.5 max-sm:size-9 max-sm:px-0" aria-label="View the console as someone else">
          <EyeIcon aria-hidden />
          <span className="max-sm:hidden">View as</span>
        </Button>
      }
    />
  );
}

/**
 * Owner-only "see the console as someone else" picker. Switching keeps you on the same page
 * (except /access, which they can't see) and reloads, because the root layout — nav and the
 * View as strip — only re-renders on a document request.
 */
export function ViewAsMenu({ current, trigger }: { current: string | null; trigger: ReactElement }) {
  const [open, setOpen] = useState(false);
  const [people, setPeople] = useState<AccessPerson[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const { isPending, run } = useConsoleAction();

  async function load() {
    setLoadError(null);
    try {
      const res = await fetch("/api/access", { cache: "no-store" });
      const body = (await res.json()) as AccessDirectory | { error: string };
      if ("error" in body) throw new Error(body.error);
      setPeople(body.people.filter((p) => p.role !== "owner"));
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Couldn't load people.");
    }
  }

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (people ?? []).filter((p) => !q || p.email.includes(q) || (p.note ?? "").toLowerCase().includes(q));
  }, [people, query]);

  async function pick(email: string) {
    if (email === current) return setOpen(false);
    const ack = await run(() => postAccess({ op: "view-as", email }), { saving: "Switching view…" });
    if (!ack.ok) return;
    if (window.location.pathname.startsWith("/access")) window.location.assign("/");
    else window.location.reload();
  }

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
        else if (people === null) void load();
      }}
    >
      <Popover.Trigger render={trigger} disabled={isPending} data-testid="view-as-menu" />
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Popover.Popup
            className={cn(
              "flex w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-lg outline-none",
              "origin-[var(--transform-origin)] transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
            )}
          >
            <div className="flex flex-col gap-0.5 border-b px-3 pt-3 pb-2.5">
              <Popover.Title className="text-sm font-medium">See the console as…</Popover.Title>
              <Popover.Description className="text-xs text-muted-foreground">
                Pages and sections exactly as they see them. Nothing changes for them.
              </Popover.Description>
            </div>
            {people && people.length > 6 ? (
              <div className="relative border-b p-2">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-4.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                  autoFocus
                  placeholder="Search people"
                  aria-label="Search people"
                  className="h-9 pl-8"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            ) : null}
            <ul className="max-h-80 overflow-y-auto p-1.5" role="listbox" aria-label="People">
              {loadError ? (
                <li className="px-2 py-3 text-sm text-destructive">{loadError}</li>
              ) : people === null ? (
                [0, 1, 2].map((i) => (
                  <li key={i} className="flex h-12 items-center gap-3 px-2" aria-hidden>
                    <span className="size-7 animate-pulse rounded-full bg-muted" />
                    <span className="h-3 w-40 animate-pulse rounded bg-muted" />
                  </li>
                ))
              ) : shown.length === 0 ? (
                <li className="px-2 py-3 text-sm text-muted-foreground">
                  {people.length ? "No one matches." : "No one else has access yet — add people on the Access page."}
                </li>
              ) : (
                shown.map((p) => {
                  const on = p.email === current;
                  return (
                    <li key={p.email}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={on}
                        disabled={isPending}
                        onClick={() => void pick(p.email)}
                        data-testid={`view-as-${p.email}`}
                        className={cn(
                          "flex min-h-12 w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                          on ? "bg-muted" : "hover:bg-muted/60",
                        )}
                      >
                        <PersonAvatar email={p.email} size="sm" muted={p.role === "removed"} />
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate text-sm font-medium">{p.email}</span>
                          {p.note ? <span className="truncate text-xs text-muted-foreground">{p.note}</span> : null}
                        </span>
                        {on ? <CheckIcon className="size-4 shrink-0 text-primary" aria-hidden /> : <RoleBadge role={p.role} />}
                      </button>
                    </li>
                  );
                })
              )}
            </ul>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
