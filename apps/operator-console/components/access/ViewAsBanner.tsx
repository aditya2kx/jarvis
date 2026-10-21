"use client";

import { useState } from "react";
import { EyeIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { AccessDirectory } from "@/lib/access/people";

/** Owner-only strip while "View as" is on: the whole console renders with that person's access. */
export function ViewAsBanner({ email }: { email: string }) {
  const { isPending, run } = useConsoleAction();
  const [others, setOthers] = useState<string[] | null>(null);

  async function loadPeople(open: boolean) {
    if (!open || others) return;
    try {
      const res = await fetch("/api/access", { cache: "no-store" });
      const body = (await res.json()) as AccessDirectory;
      setOthers(body.people.filter((p) => p.role !== "owner").map((p) => p.email));
    } catch {
      setOthers([]);
    }
  }

  // Full loads: the root layout (nav, this strip) only re-renders on a document request.
  async function switchTo(next: string | null) {
    if (!next || next === email) return;
    const ack = await run(() => postAccess({ op: "view-as", email: next }), { saving: "Switching view…" });
    if (ack.ok) window.location.reload();
  }

  async function exit() {
    const ack = await run(() => postAccess({ op: "view-as", email: null }), {
      saving: "Switching back…",
      done: "Back to your own view.",
    });
    if (ack.ok) window.location.assign("/access");
  }

  const items = (others ?? [email]).map((e) => ({ value: e, label: e }));

  return (
    <div
      role="status"
      data-testid="view-as-banner"
      className="flex shrink-0 items-center gap-2 border-b border-sky-500/30 bg-sky-500/10 px-4 py-1.5 text-sm text-sky-900 md:px-6 dark:text-sky-200"
    >
      <EyeIcon className="size-4 shrink-0" aria-hidden />
      <span className="shrink-0 max-sm:sr-only">Viewing as</span>
      <Select value={email} onValueChange={(v) => void switchTo(v)} onOpenChange={(o) => void loadPeople(o)} items={items}>
        <SelectTrigger
          size="sm"
          aria-label="Switch who you are viewing as"
          className="h-7 min-w-0 max-w-64 border-sky-500/30 bg-background/60 font-semibold text-foreground"
          disabled={isPending}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((i) => (
            <SelectItem key={i.value} value={i.value}>
              {i.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="min-w-0 flex-1 truncate opacity-75 max-md:hidden">you see exactly what they see</span>
      <Button size="sm" variant="outline" className="ml-auto h-7 shrink-0 bg-background/60" disabled={isPending} onClick={exit}>
        Exit
      </Button>
    </div>
  );
}
