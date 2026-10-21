"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { KeyRoundIcon, UserPlusIcon } from "lucide-react";
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
import { PageIcon, PersonAvatar, RoleBadge } from "./visuals";
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

  const seeing = rows.filter((p) => valueFor(p) !== null).length;

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
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-xl" data-testid="page-access-drawer">
        <SheetHeader className="border-b pb-4">
          <div className="flex items-center gap-3 pr-8">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <PageIcon page={page} className="size-5" />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <SheetTitle className="truncate text-base">Who can see {meta.label}</SheetTitle>
              <SheetDescription className="text-xs">
                {people === null
                  ? "Whole page or just some sections. Seeing a section means they can use its buttons too."
                  : `${seeing} of ${rows.length} people · seeing a section means they can use its buttons too`}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-2 overflow-y-auto px-4 py-5">
          {loadError ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {loadError}
            </p>
          ) : people === null ? (
            <div className="flex flex-col gap-2" aria-busy>
              <span className="sr-only">Loading people…</span>
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="flex h-16 items-center gap-3 rounded-xl border px-3">
                  <span className="size-9 animate-pulse rounded-full bg-muted" />
                  <span className="h-3 w-40 animate-pulse rounded bg-muted" />
                </div>
              ))}
            </div>
          ) : (
            rows.map((p) => {
              const v = valueFor(p);
              const mode = modeOf(v);
              const isOwner = p.role === "owner";
              const edited = !isOwner && !same(v, pageValue(p, page));
              return (
                <div
                  key={p.email}
                  className={cn(
                    "overflow-hidden rounded-xl border transition-colors",
                    edited ? "border-primary/50 bg-primary/[0.04]" : "bg-card",
                  )}
                  data-testid={`page-access-row-${p.email}`}
                >
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
                    <PersonAvatar email={p.email} muted={mode === "none"} />
                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className={cn("truncate text-sm font-medium", mode === "none" && "text-muted-foreground")}>
                        {p.email}
                      </span>
                      <span className="truncate text-xs text-muted-foreground">
                        {edited ? "Unsaved change" : p.note ?? (isOwner ? "Always sees everything" : "\u00a0")}
                      </span>
                    </div>
                    {isOwner ? (
                      <RoleBadge role="owner" />
                    ) : (
                      <div
                        className="flex items-center gap-0.5 rounded-lg bg-muted p-0.5 max-sm:w-full"
                        role="radiogroup"
                        aria-label={`${p.email} access to ${meta.label}`}
                      >
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
                              "h-7 rounded-md px-2.5 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 max-sm:h-9 max-sm:flex-1",
                              mode === m
                                ? "bg-background text-foreground shadow-sm"
                                : "text-muted-foreground hover:text-foreground",
                            )}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {mode === "some" && Array.isArray(v) ? (
                    <div className="grid gap-x-4 border-t bg-muted/30 px-3 py-2 sm:grid-cols-2">
                      {meta.sections.map((s) => (
                        <label
                          key={s.id}
                          className={cn(
                            "flex min-h-9 cursor-pointer items-center gap-2.5 rounded-md px-1 text-sm transition-colors hover:text-foreground",
                            v.includes(s.id) ? "text-foreground" : "text-muted-foreground",
                          )}
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
              className="mt-2 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                addPerson();
              }}
            >
              <Input
                type="email"
                inputMode="email"
                placeholder="Add someone by Google email"
                className="h-10"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                aria-label="Add someone by Google email"
              />
              <Button type="submit" variant="outline" className="h-10" disabled={!isValidEmail(newEmail)}>
                <UserPlusIcon data-icon="inline-start" />
                Add
              </Button>
            </form>
          ) : null}
        </div>

        <SheetFooter className="mt-0 gap-3 border-t bg-popover">
          {stage || error ? (
            <p className={cn("text-xs", error ? "text-destructive" : "text-muted-foreground")} role="status">
              {error ?? stage}
            </p>
          ) : null}
          <div className="flex items-center gap-3">
            <p className="min-w-0 flex-1 text-xs text-muted-foreground">
              Other pages stay as they are; anyone left with no pages loses sign-in.{" "}
              <Link href="/access" className="font-medium text-foreground underline-offset-2 hover:underline">
                All people and pages
              </Link>
            </p>
            <Button
              className="min-h-11 min-w-28 sm:min-h-9"
              disabled={isPending || changed.length === 0}
              onClick={save}
              data-testid="save-page-access"
            >
              {changed.length ? `Save ${changed.length} change${changed.length === 1 ? "" : "s"}` : "No changes"}
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
