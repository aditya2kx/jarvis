"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { KeyRoundIcon, Loader2Icon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { RoleBadge } from "./AccessManager";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { accessPage, type PageId } from "@/lib/access/registry";
import { NO_ACCESS, isValidEmail, setPageGrant, type PageGrant } from "@/lib/access/policy";
import type { AccessDirectory, AccessPerson } from "@/lib/access/people";
import { cn } from "@/lib/utils";

type Mode = "none" | "all" | "some";

function modeOf(v: PageGrant | null): Mode {
  return v === null ? "none" : v === "all" ? "all" : "some";
}

function pageValue(p: AccessPerson, page: PageId): PageGrant | null {
  if (p.grant.full) return "all";
  return p.grant.pages[page] ?? null;
}

function same(a: PageGrant | null, b: PageGrant | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

const MODES: [Mode, string][] = [
  ["none", "None"],
  ["all", "Whole page"],
  ["some", "Sections"],
];

/** Owner-only "Manage access" in a page header: who can see this page, edited in place. */
export function PageAccessDrawer({ page }: { page: PageId }) {
  const meta = accessPage(page)!;
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [people, setPeople] = useState<AccessPerson[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, PageGrant | null>>({});
  const [newEmail, setNewEmail] = useState("");
  const [added, setAdded] = useState<string[]>([]);
  const { isPending, stage, error, run } = useConsoleAction();

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await fetch("/api/access", { cache: "no-store" });
      const body = (await res.json()) as AccessDirectory | { error: string };
      if ("error" in body) throw new Error(body.error);
      setPeople(body.people);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Couldn't load access.");
    }
  }, []);

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) return;
    setDrafts({});
    setAdded([]);
    setNewEmail("");
    void load();
  }

  const rows = useMemo(() => {
    const known = people ?? [];
    const extra = added
      .filter((e) => !known.some((p) => p.email === e))
      .map(
        (email): AccessPerson => ({
          email,
          role: "custom",
          grant: NO_ACCESS,
          summary: "",
          signIn: false,
          changedBy: null,
          changedAt: null,
          note: null,
        }),
      );
    return [...known, ...extra];
  }, [people, added]);

  const valueFor = (p: AccessPerson) => (p.email in drafts ? drafts[p.email] : pageValue(p, page));
  const changed = rows.filter((p) => p.role !== "owner" && !same(valueFor(p), pageValue(p, page)));

  function setValue(p: AccessPerson, v: PageGrant | null) {
    setDrafts((d) => ({ ...d, [p.email]: v }));
  }

  function addPerson() {
    const email = newEmail.trim().toLowerCase();
    if (!isValidEmail(email)) return;
    const existing = rows.find((p) => p.email === email);
    if (existing) {
      if (existing.role !== "owner") setValue(existing, "all");
    } else {
      setAdded((a) => [...a, email]);
      setDrafts((d) => ({ ...d, [email]: "all" }));
    }
    setNewEmail("");
  }

  async function save() {
    const ack = await run(
      async () => {
        for (const p of changed) {
          const r = await postAccess({ op: "save", email: p.email, grant: setPageGrant(p.grant, page, valueFor(p)) });
          if (!r.ok) return { ok: false as const, error: `${p.email}: ${r.error}` };
        }
        return { ok: true as const, message: `Saved ${meta.label} access for ${changed.length} ${changed.length === 1 ? "person" : "people"}.` };
      },
      { saving: "Saving…" },
    );
    if (ack.ok) {
      setOpen(false);
      router.refresh();
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetTrigger
        render={
          <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" data-testid="manage-access">
            <KeyRoundIcon data-icon="inline-start" />
            Manage access
          </Button>
        }
      />
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg" data-testid="page-access-drawer">
        <SheetHeader>
          <SheetTitle>Who can see {meta.label}</SheetTitle>
          <SheetDescription>
            Give the whole page or pick sections. Seeing a section means they can use its buttons too.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-3 px-4">
          {loadError ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {loadError}
            </p>
          ) : people === null ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" aria-hidden /> Loading people…
            </div>
          ) : (
            rows.map((p) => {
              const v = valueFor(p);
              const mode = modeOf(v);
              const isOwner = p.role === "owner";
              return (
                <div key={p.email} className="rounded-lg border" data-testid={`page-access-row-${p.email}`}>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm font-medium">{p.email}</span>
                      {p.note ? <span className="truncate text-xs text-muted-foreground">{p.note}</span> : null}
                    </div>
                    {isOwner ? (
                      <RoleBadge role="owner" />
                    ) : (
                      <div className="flex items-center gap-0.5 rounded-lg border border-border p-0.5" role="radiogroup" aria-label={`${p.email} access to ${meta.label}`}>
                        {MODES.map(([m, label]) => (
                          <button
                            key={m}
                            type="button"
                            role="radio"
                            aria-checked={mode === m}
                            disabled={isPending}
                            onClick={() =>
                              setValue(p, m === "none" ? null : m === "all" ? "all" : mode === "some" ? v : [meta.sections[0].id])
                            }
                            className={cn(
                              "h-7 rounded-md px-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 max-md:h-9",
                              mode === m ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                            )}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {mode === "some" && Array.isArray(v) ? (
                    <div className="grid gap-x-4 gap-y-1 border-t bg-muted/30 px-3 py-2 sm:grid-cols-2">
                      {meta.sections.map((s) => (
                        <label
                          key={s.id}
                          className="flex min-h-9 cursor-pointer items-center gap-2.5 rounded-md px-1 text-sm text-muted-foreground hover:text-foreground"
                        >
                          <Checkbox
                            checked={v.includes(s.id)}
                            disabled={isPending}
                            onCheckedChange={(on) => {
                              const next = on ? [...v, s.id] : v.filter((x) => x !== s.id);
                              setValue(p, next.length === 0 ? null : next.length === meta.sections.length ? "all" : next);
                            }}
                          />
                          {s.label}
                        </label>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}

          {people !== null ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                addPerson();
              }}
            >
              <Input
                type="email"
                inputMode="email"
                placeholder="Add someone by Google email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                aria-label="Add someone by Google email"
              />
              <Button type="submit" variant="outline" disabled={!isValidEmail(newEmail)}>
                Add
              </Button>
            </form>
          ) : null}
          <p className="text-xs text-muted-foreground">
            Other pages are unchanged. Someone left with no pages at all loses sign-in too.{" "}
            <Link href="/access" className="underline underline-offset-2 hover:text-foreground">
              All people and pages
            </Link>
          </p>
        </div>

        <SheetFooter className="flex-row flex-wrap items-center gap-2 border-t">
          {stage || error ? (
            <p className={cn("w-full text-xs", error ? "text-destructive" : "text-muted-foreground")} role="status">
              {error ?? stage}
            </p>
          ) : null}
          <Button className="ml-auto min-h-11 sm:min-h-8" disabled={isPending || changed.length === 0} onClick={save}>
            {changed.length ? `Save ${changed.length} change${changed.length === 1 ? "" : "s"}` : "Save"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
