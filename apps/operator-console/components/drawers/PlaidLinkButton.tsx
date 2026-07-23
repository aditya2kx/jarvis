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

/** localStorage (not sessionStorage) — OAuth popup is a separate window and
 *  does not share sessionStorage with the opener. */
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
          localStorage.removeItem(LINK_TOKEN_KEY);
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
    if (eventName === "OPEN_OAUTH") {
      setMessage(
        "Chase login opened in a popup — complete phone/code there. When it finishes you should return here automatically.",
      );
    }
    if (eventName === "EXIT") {
      setMessage((prev) =>
        prev?.includes("syncing") || prev?.includes("Linked ")
          ? prev
          : "Link closed before finishing — try Link bank again (allow popups).",
      );
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
          "Starting Link… Chase will open a popup for phone/login — keep this tab open until sync finishes.",
        );
        const t = await createPlaidLinkTokenAction();
        localStorage.setItem(LINK_TOKEN_KEY, t);
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
