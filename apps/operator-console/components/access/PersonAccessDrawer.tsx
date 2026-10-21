"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CheckIcon, ChevronDownIcon, HistoryIcon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageGrantRow } from "./PageGrantRow";
import { AccessHistoryList } from "./AccessHistoryList";
import { PersonAvatar, RoleBadge } from "./visuals";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { ACCESS_PAGES } from "@/lib/access/registry";
import { ACCESS_PRESETS, matchPreset } from "@/lib/access/presets";
import {
  NO_ACCESS,
  canPage,
  isValidEmail,
  normalizeGrant,
  sectionsGranted,
  type Grant,
  type GrantVersion,
} from "@/lib/access/policy";
import type { AccessPerson } from "@/lib/access/people";
import { cn } from "@/lib/utils";

const TOTAL_SECTIONS = ACCESS_PAGES.reduce((n, p) => n + p.sections.length, 0);

function reach(grant: Grant): string {
  if (grant.full) return "Every page and section, including ones added later";
  const pages = ACCESS_PAGES.filter((p) => canPage(grant, p.id));
  if (!pages.length) return "Nothing yet — pick a starting point or a page";
  const sections = pages.reduce((n, p) => n + sectionsGranted(grant, p.id), 0);
  return `${pages.length} ${pages.length === 1 ? "page" : "pages"} · ${sections} of ${TOTAL_SECTIONS} sections`;
}

/**
 * Add a person or edit one person's pages and sections. `person === null` = adding.
 * Give it a fresh `key` per open so the form starts from the saved grant.
 */
export function PersonAccessDrawer({
  open,
  onOpenChange,
  person,
  history,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  person: AccessPerson | null;
  history: GrantVersion[];
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [grant, setGrant] = useState<Grant>(person?.grant ?? NO_ACCESS);
  const [note, setNote] = useState(person?.note ?? "");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const { isPending, stage, error, run } = useConsoleAction();

  const adding = person === null;
  const mine = history.filter((h) => h.email === person?.email);
  const hasAny = grant.full || Object.keys(grant.pages).length > 0;
  const preset = matchPreset(grant);
  const dirty =
    adding ||
    person.role === "default" ||
    note.trim() !== (person.note ?? "") ||
    JSON.stringify(normalizeGrant(grant)) !== JSON.stringify(normalizeGrant(person.grant));

  function undoTo(target: string, prev: AccessPerson | null) {
    if (!prev) return postAccess({ op: "revoke", email: target, note: "Undo" });
    if (prev.role === "removed") return postAccess({ op: "revoke", email: target, note: "Undo" });
    return postAccess({ op: "save", email: target, grant: prev.grant, note: prev.note ?? "Undo" });
  }

  async function save(next: Grant, revoke = false) {
    if (adding && !isValidEmail(email)) {
      setInvalid("Enter the person's Google account email.");
      return;
    }
    const target = adding ? email.trim().toLowerCase() : person.email;
    const ack = await run(
      () =>
        revoke
          ? postAccess({ op: "revoke", email: target, note })
          : postAccess({ op: "save", email: target, grant: next, note }),
      {
        saving: revoke ? "Removing…" : "Saving…",
        toastAction: {
          label: "Undo",
          onClick: () => {
            void undoTo(target, person).then(() => router.refresh());
          },
        },
      },
    );
    if (ack.ok) {
      onOpenChange(false);
      router.refresh();
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-xl" data-testid="person-access-drawer">
        <SheetHeader className="border-b pb-4">
          <div className="flex items-center gap-3 pr-8">
            {adding ? null : <PersonAvatar email={person.email} size="lg" muted={person.role === "removed"} />}
            <div className="flex min-w-0 flex-col gap-1">
              <SheetTitle className="truncate text-base">{adding ? "Add person" : person.email}</SheetTitle>
              {adding ? null : (
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <RoleBadge role={person.role} />
                  {person.signIn === null ? null : (
                    <span className="inline-flex items-center gap-1">
                      <span
                        aria-hidden
                        className={cn("size-1.5 rounded-full", person.signIn ? "bg-emerald-500" : "bg-muted-foreground/40")}
                      />
                      {person.signIn ? "Can sign in" : "Can't sign in"}
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
          <SheetDescription className={adding ? "" : "sr-only"}>
            They sign in with this Google account. Seeing a section means they can use its buttons too.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-6 overflow-y-auto px-4 py-5">
          {adding ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="access-email">Google account email</Label>
              <Input
                id="access-email"
                type="email"
                inputMode="email"
                autoComplete="off"
                autoFocus
                placeholder="name@mypalmetto.co"
                className="h-10"
                value={email}
                aria-invalid={invalid ? true : undefined}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setInvalid(null);
                }}
              />
              {invalid ? <p className="text-xs text-destructive">{invalid}</p> : null}
            </div>
          ) : null}

          <section className="flex flex-col gap-2" aria-labelledby="access-presets">
            <div className="flex items-baseline justify-between">
              <h3 id="access-presets" className="text-sm font-medium">
                Start from
              </h3>
              {hasAny && !preset ? <span className="text-xs text-muted-foreground">Customized</span> : null}
            </div>
            <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-labelledby="access-presets">
              {ACCESS_PRESETS.map((p) => {
                const on = preset?.id === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    disabled={isPending}
                    onClick={() => setGrant(p.grant)}
                    data-testid={`preset-${p.id}`}
                    className={cn(
                      "group relative flex min-h-16 flex-col items-start gap-0.5 rounded-xl border px-3 py-2.5 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                      on ? "border-primary bg-primary/[0.06] shadow-sm" : "hover:border-foreground/20 hover:bg-muted/50",
                    )}
                  >
                    <span className="flex w-full items-center justify-between gap-2 text-sm font-medium">
                      {p.label}
                      <span
                        className={cn(
                          "flex size-4 items-center justify-center rounded-full border transition-colors",
                          on ? "border-primary bg-primary text-primary-foreground" : "border-border",
                        )}
                      >
                        {on ? <CheckIcon className="size-3" strokeWidth={3} /> : null}
                      </span>
                    </span>
                    <span className="text-xs leading-snug text-muted-foreground">{p.hint}</span>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="flex flex-col gap-2" aria-labelledby="access-pages">
            <div className="flex items-baseline justify-between">
              <h3 id="access-pages" className="text-sm font-medium">
                Pages and sections
              </h3>
              <span className="text-xs text-muted-foreground max-sm:hidden">Tick a page, or open it to pick sections</span>
            </div>
            <div className="flex flex-col gap-1.5">
              {ACCESS_PAGES.map((p) => (
                <PageGrantRow key={p.id} page={p} grant={grant} onChange={setGrant} disabled={isPending} />
              ))}
            </div>
          </section>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="access-note">Note</Label>
            <Input
              id="access-note"
              placeholder="Optional — e.g. Assistant manager, Austin"
              className="h-10"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
            />
          </div>

          {!adding && mine.length ? (
            <section className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setShowHistory((s) => !s)}
                aria-expanded={showHistory}
                className="flex items-center gap-2 self-start rounded-md text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <HistoryIcon className="size-4" aria-hidden />
                History · {mine.length} {mine.length === 1 ? "change" : "changes"}
                <ChevronDownIcon className={cn("size-4 transition-transform", showHistory && "rotate-180")} aria-hidden />
              </button>
              {showHistory ? <AccessHistoryList entries={mine} showEmail={false} /> : null}
            </section>
          ) : null}
        </div>

        <SheetFooter className="mt-0 gap-3 border-t bg-popover">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="text-muted-foreground" data-testid="access-reach">
              <span className="font-medium text-foreground">Will see: </span>
              {reach(grant)}
            </span>
          </div>
          {stage || error ? (
            <p className={cn("text-xs", error ? "text-destructive" : "text-muted-foreground")} role="status">
              {error ?? stage}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {!adding && person.role !== "removed" ? (
              confirmRemove ? (
                <div className="flex items-center gap-2">
                  <Button
                    variant="destructive"
                    className="min-h-11 sm:min-h-9"
                    disabled={isPending}
                    onClick={() => save(NO_ACCESS, true)}
                    data-testid="confirm-remove"
                  >
                    Remove all access
                  </Button>
                  <Button variant="ghost" className="min-h-11 sm:min-h-9" onClick={() => setConfirmRemove(false)}>
                    Keep
                  </Button>
                </div>
              ) : (
                <Button
                  variant="ghost"
                  className="min-h-11 text-destructive hover:bg-destructive/10 hover:text-destructive sm:min-h-9"
                  disabled={isPending}
                  onClick={() => setConfirmRemove(true)}
                >
                  Remove access
                </Button>
              )
            ) : null}
            <div className="ml-auto flex items-center gap-2">
              <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                className="min-h-11 min-w-24 sm:min-h-9"
                disabled={isPending || !dirty || (adding && !hasAny)}
                onClick={() => save(grant)}
                data-testid="save-person"
              >
                {adding ? "Add person" : "Save changes"}
              </Button>
            </div>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
