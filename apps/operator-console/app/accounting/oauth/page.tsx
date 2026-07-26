"use client";

/**
 * Plaid OAuth return page (Chase and other OAuth banks).
 *
 * Retained for optional non-IAP deployments that set `PLAID_REDIRECT_URI`.
 * Current Cloud Run prod intentionally leaves that unset (IAP blocks the
 * redirect), so desktop Chase OAuth uses popup → opener only and this route
 * is not on the live Link path.
 *
 * After bank login/OTP, Plaid redirects the *popup* here with ?oauth_state_id=….
 * Link token must live in localStorage (sessionStorage is per-window and empty
 * in the popup). We reinitialize Link with receivedRedirectUri, exchange the
 * public_token, then close the popup or send the opener back to Accounting.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { usePlaidLink } from "react-plaid-link";
import { exchangePlaidPublicTokenAction } from "@/app/accounting/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { okAck } from "@/lib/actions/types";

const LINK_TOKEN_KEY = "plaid_link_token";

export default function PlaidOAuthReturnPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState("Resuming bank login…");
  const { isPending, stage, error, run } = useConsoleAction();

  const receivedRedirectUri = useMemo(() => {
    if (typeof window === "undefined") return undefined;
    return window.location.href;
  }, []);

  useEffect(() => {
    const t = localStorage.getItem(LINK_TOKEN_KEY);
    if (!t) {
      setStatus(
        "Missing Link session in this window. Close this tab, keep Accounting open, and click Link bank again.",
      );
      return;
    }
    setToken(t);
  }, []);

  const finish = useCallback(
    (msg: string) => {
      setStatus(msg);
      // Prefer closing the OAuth popup and refreshing the opener.
      try {
        if (window.opener && !window.opener.closed) {
          window.opener.location.href = "/accounting";
          window.close();
          return;
        }
      } catch {
        // cross-origin opener access can throw — fall through
      }
      router.replace("/accounting");
      router.refresh();
    },
    [router],
  );

  const onSuccess = useCallback(
    (publicToken: string) => {
      void run(async () => {
        setStatus("Linked — syncing transactions (can take a minute)…");
        localStorage.removeItem(LINK_TOKEN_KEY);
        const ack = await exchangePlaidPublicTokenAction(publicToken);
        if (!ack.ok) return ack;
        const result = ack.data!;
        finish(`Done — synced +${result.sync.added}. Returning to Accounting…`);
        return okAck({
          message: `Done — synced +${result.sync.added}.`,
        });
      });
    },
    [finish, run],
  );

  const onExit = useCallback(() => {
    setStatus((s) =>
      s.startsWith("Linked") || s.startsWith("Done")
        ? s
        : "Link exited before finishing — close this window and try Link bank again from Accounting.",
    );
  }, []);

  const { open, ready } = usePlaidLink({
    token,
    receivedRedirectUri,
    onSuccess,
    onExit,
  });

  useEffect(() => {
    if (ready && token && receivedRedirectUri) {
      open();
    }
  }, [ready, token, receivedRedirectUri, open]);

  const feedback = error || stage || status;

  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 p-6">
      <p className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>
        {feedback}
      </p>
      {isPending ? <p className="text-xs text-muted-foreground">Working…</p> : null}
      <a href="/accounting" className="text-xs underline">
        Back to Accounting
      </a>
    </div>
  );
}
