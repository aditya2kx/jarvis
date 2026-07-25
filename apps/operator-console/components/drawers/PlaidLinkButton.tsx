"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePlaidLink } from "react-plaid-link";
import { Button } from "@/components/ui/button";
import {
  createPlaidLinkTokenAction,
  exchangePlaidPublicTokenAction,
  syncPlaidNowAction,
} from "@/app/accounting/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { okAck } from "@/lib/actions/types";

/** localStorage (not sessionStorage) — OAuth popup is a separate window and
 *  does not share sessionStorage with the opener. */
const LINK_TOKEN_KEY = "plaid_link_token";

export function PlaidLinkButton({ linked }: { linked: boolean }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [wantOpen, setWantOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { isPending, stage, error, run } = useConsoleAction();

  const onSuccess = useCallback(
    (publicToken: string) => {
      void run(async () => {
        localStorage.removeItem(LINK_TOKEN_KEY);
        setMessage("Linked — syncing Chase transactions (can take a minute)…");
        const ack = await exchangePlaidPublicTokenAction(publicToken);
        if (!ack.ok) return ack;
        const result = ack.data!;
        setMessage(
          `Linked ${result.itemId.slice(0, 8)}… — synced +${result.sync.added} / ~${result.sync.modified} / -${result.sync.removed}`,
        );
        setToken(null);
        setWantOpen(false);
        router.refresh();
        return okAck({
          message: `Linked — synced +${result.sync.added} / ~${result.sync.modified} / -${result.sync.removed}`,
        });
      });
    },
    [router, run],
  );

  const onEvent = useCallback((eventName: string, metadata: Record<string, unknown>) => {
    if (eventName === "OPEN_OAUTH") {
      setMessage(
        "Chase login opened in a popup — complete phone/code there, then return to this tab (keep it open).",
      );
    }
    if (eventName === "ERROR") {
      const code = String(metadata?.error_code || "unknown");
      const msg = String(metadata?.error_message || "Link error");
      setMessage(`Plaid error (${code}): ${msg}`);
    }
    if (eventName === "EXIT") {
      setMessage((prev) =>
        prev?.includes("syncing") || prev?.includes("Linked ") || prev?.includes("Plaid error")
          ? prev
          : "Link closed before finishing — try Link bank again (allow popups for this site).",
      );
    }
  }, []);

  const { open, ready } = usePlaidLink({
    token,
    onSuccess,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- PlaidLinkOnEvent metadata is a wide SDK type
    onEvent: onEvent as any,
    onExit: (err) => {
      if (!err) return;
      setMessage(
        `Link exited: ${err.error_code || "error"} — ${err.display_message || err.error_message || "try again"}`,
      );
    },
  });

  useEffect(() => {
    if (wantOpen && ready && token) {
      open();
      setWantOpen(false);
    }
  }, [wantOpen, ready, token, open]);

  function startLink() {
    void run(async () => {
      setMessage(
        "Starting Link… Chase will open a popup for phone/login — keep this tab open until sync finishes.",
      );
      const ack = await createPlaidLinkTokenAction();
      if (!ack.ok) return ack;
      const t = ack.data!;
      localStorage.setItem(LINK_TOKEN_KEY, t);
      setToken(t);
      setWantOpen(true);
      return okAck({ message: "Link ready — complete bank login in the popup." });
    });
  }

  function syncNow() {
    void run(async () => {
      const ack = await syncPlaidNowAction();
      if (!ack.ok) return ack;
      const result = ack.data!;
      setMessage(
        `Sync ok — +${result.sync.added} / ~${result.sync.modified} / -${result.sync.removed}`,
      );
      router.refresh();
      return okAck({
        message: `Sync ok — +${result.sync.added} / ~${result.sync.modified} / -${result.sync.removed}`,
      });
    });
  }

  const feedback = error || stage || message;

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
      {feedback ? (
        <p className={`text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
          {feedback}
        </p>
      ) : null}
    </div>
  );
}
