"use client";

import { ChevronDownIcon, EyeIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ViewAsMenu } from "./ViewAsMenu";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";

/** Owner-only strip while "View as" is on: the whole console renders with that person's access. */
export function ViewAsBanner({ email }: { email: string }) {
  const { isPending, run } = useConsoleAction();

  async function exit() {
    const ack = await run(() => postAccess({ op: "view-as", email: null }), {
      saving: "Switching back…",
      done: "Back to your own view.",
    });
    // Full load: the root layout (nav, this strip) only re-renders on a document request.
    if (ack.ok) window.location.reload();
  }

  return (
    <div
      role="status"
      data-testid="view-as-banner"
      className="flex shrink-0 items-center gap-2 border-b border-sky-500/30 bg-sky-500/10 px-4 py-1.5 text-sm text-sky-900 md:px-6 dark:text-sky-200"
    >
      <EyeIcon className="size-4 shrink-0" aria-hidden />
      <span className="shrink-0 max-sm:sr-only">Viewing as</span>
      <ViewAsMenu
        current={email}
        trigger={
          <button
            type="button"
            aria-label={`Viewing as ${email} — switch person`}
            className="flex h-7 min-w-0 items-center gap-1 rounded-md border border-sky-500/30 bg-background/60 px-2 font-semibold text-foreground transition-colors hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="truncate">{email}</span>
            <ChevronDownIcon className="size-3.5 shrink-0 opacity-60" aria-hidden />
          </button>
        }
      />
      <span className="min-w-0 flex-1 truncate opacity-75 max-md:hidden">you see exactly what they see</span>
      <Button size="sm" variant="outline" className="ml-auto h-7 shrink-0 bg-background/60" disabled={isPending} onClick={exit}>
        Exit
      </Button>
    </div>
  );
}
