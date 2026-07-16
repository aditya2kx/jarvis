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

const LINK_TOKEN_KEY = "plaid_link_token";

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
          sessionStorage.removeItem(LINK_TOKEN_KEY);
          setMessage("Linked — syncing Chase transactions (can take a minute)…");
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

  const onEvent = useCallback((eventName: string) => {
    // Chase opens an OAuth popup/tab — if the browser blocks it, Link looks stuck.
    if (eventName === "OPEN_OAUTH") {
      setMessage(
        "Chase login should open in a popup or new tab — allow popups for this site if nothing appears.",
      );
    }
    if (eventName === "EXIT" || eventName === "HANDOFF") {
      // leave message as-is; EXIT often fires when popup closes mid-flow
    }
  }, []);

  const { open, ready } = usePlaidLink({
    token,
    onSuccess,
    onEvent,
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
        setMessage(
          "Starting Link… After you pick Chase, a login popup/tab should open — allow popups if blocked.",
        );
        const t = await createPlaidLinkTokenAction();
        sessionStorage.setItem(LINK_TOKEN_KEY, t);
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
