"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { usePlaidLink } from "react-plaid-link";
import { Button } from "@/components/ui/button";
import {
  createPlaidLinkTokenAction,
  exchangePlaidPublicTokenAction,
  syncPlaidNowAction,
} from "@/app/accounting/actions";

export function PlaidLinkButton({ linked }: { linked: boolean }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [wantOpen, setWantOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const onSuccess = useCallback(
    (publicToken: string) => {
      startTransition(async () => {
        try {
          const result = await exchangePlaidPublicTokenAction(publicToken);
          setMessage(
            `Linked ${result.itemId.slice(0, 8)}… — synced +${result.sync.added} / ~${result.sync.modified} / -${result.sync.removed}`,
          );
          setToken(null);
          setWantOpen(false);
          router.refresh();
        } catch (e) {
          setMessage(`Link failed: ${e instanceof Error ? e.message : String(e)}`);
        }
      });
    },
    [router],
  );

  const { open, ready } = usePlaidLink({
    token,
    onSuccess,
  });

  useEffect(() => {
    if (wantOpen && ready && token) {
      open();
      setWantOpen(false);
    }
  }, [wantOpen, ready, token, open]);

  function startLink() {
    startTransition(async () => {
      try {
        setMessage(null);
        const t = await createPlaidLinkTokenAction();
        setToken(t);
        setWantOpen(true);
      } catch (e) {
        setMessage(`Could not start Link: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  function syncNow() {
    startTransition(async () => {
      try {
        const result = await syncPlaidNowAction();
        setMessage(
          `Sync ok — +${result.sync.added} / ~${result.sync.modified} / -${result.sync.removed}`,
        );
        router.refresh();
      } catch (e) {
        setMessage(`Sync failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {!linked ? (
          <Button size="sm" disabled={isPending} onClick={startLink}>
            {isPending ? "Starting…" : "Link bank account…"}
          </Button>
        ) : (
          <>
            <Button size="sm" variant="outline" disabled={isPending} onClick={syncNow}>
              {isPending ? "Syncing…" : "Sync now"}
            </Button>
            <Button size="sm" variant="ghost" disabled={isPending} onClick={startLink}>
              Relink…
            </Button>
          </>
        )}
      </div>
      {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
    </div>
  );
}
