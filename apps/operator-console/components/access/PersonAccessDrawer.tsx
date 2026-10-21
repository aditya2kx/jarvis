"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
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
import { Switch } from "@/components/ui/switch";
import { PageGrantRow } from "./PageGrantRow";
import { AccessHistoryList } from "./AccessHistoryList";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { ACCESS_PAGES } from "@/lib/access/registry";
import { NO_ACCESS, grantSummary, isValidEmail, type Grant, type GrantVersion } from "@/lib/access/policy";
import type { AccessPerson } from "@/lib/access/people";

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
  const [note, setNote] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const { isPending, stage, error, run } = useConsoleAction();

  const adding = person === null;
  const mine = history.filter((h) => h.email === person?.email);
  const hasAny = grant.full || Object.keys(grant.pages).length > 0;

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
      { saving: revoke ? "Removing…" : "Saving…" },
    );
    if (ack.ok) {
      onOpenChange(false);
      router.refresh();
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg" data-testid="person-access-drawer">
        <SheetHeader>
          <SheetTitle>{adding ? "Add person" : person.email}</SheetTitle>
          <SheetDescription>
            {adding
              ? "They sign in with this Google account. Pick the pages and sections they can see — and use."
              : "Pages and sections they can see. Seeing a section means they can use its buttons too."}
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-5 px-4">
          {adding ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="access-email">Google account email</Label>
              <Input
                id="access-email"
                type="email"
                inputMode="email"
                autoComplete="off"
                placeholder="name@example.com"
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

          <label className="flex cursor-pointer items-center justify-between gap-4 rounded-lg border px-3 py-3">
            <span className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">Full access</span>
              <span className="text-xs text-muted-foreground">Every page and section, including ones added later.</span>
            </span>
            <Switch
              checked={grant.full}
              disabled={isPending}
              onCheckedChange={(on) => setGrant(on ? { full: true, pages: {} } : NO_ACCESS)}
              aria-label="Full access"
            />
          </label>

          {!grant.full ? (
            <div className="flex flex-col gap-2">
              <div className="flex items-baseline justify-between">
                <span className="text-sm font-medium">Pages</span>
                <span className="text-xs text-muted-foreground">Tick a page for all of it, or open it to pick sections</span>
              </div>
              {ACCESS_PAGES.map((p) => (
                <PageGrantRow key={p.id} page={p} grant={grant} onChange={setGrant} disabled={isPending} />
              ))}
            </div>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="access-note">Note (optional)</Label>
            <Input
              id="access-note"
              placeholder="e.g. Assistant manager — labor and inventory"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
            />
          </div>

          <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Will have: </span>
            {grantSummary(grant)}
          </p>

          {!adding && mine.length ? (
            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium">History</span>
              <AccessHistoryList entries={mine} showEmail={false} />
            </div>
          ) : null}
        </div>

        <SheetFooter className="flex-row flex-wrap items-center gap-2 border-t">
          {stage || error ? (
            <p className={`w-full text-xs ${error ? "text-destructive" : "text-muted-foreground"}`} role="status">
              {error ?? stage}
            </p>
          ) : null}
          {!adding && person.role !== "removed" ? (
            <Button
              variant="destructive"
              className="min-h-11 sm:min-h-8"
              disabled={isPending}
              onClick={() => save(NO_ACCESS, true)}
            >
              Remove access
            </Button>
          ) : null}
          <Button
            className="ml-auto min-h-11 sm:min-h-8"
            disabled={isPending || (adding && !hasAny)}
            onClick={() => save(grant)}
          >
            {adding ? "Add" : "Save"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
