"use client";

/**
 * Plaid OAuth return page (Chase and other OAuth banks).
 * After bank login, Plaid redirects here with ?oauth_state_id=…;
 * we reinitialize Link with receivedRedirectUri so the flow can finish.
 */

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { usePlaidLink } from "react-plaid-link";
import { exchangePlaidPublicTokenAction } from "@/app/accounting/actions";

const LINK_TOKEN_KEY = "plaid_link_token";

export default function PlaidOAuthReturnPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState("Resuming Chase / bank login…");
  const [isPending, startTransition] = useTransition();

  const receivedRedirectUri = useMemo(() => {
    if (typeof window === "undefined") return undefined;
    return window.location.href;
  }, []);

  useEffect(() => {
    const t = sessionStorage.getItem(LINK_TOKEN_KEY);
    if (!t) {
      setStatus("Missing Link session — go back to Accounting and click Link bank again.");
      return;
    }
    setToken(t);
  }, []);

  const onSuccess = useCallback(
    (publicToken: string) => {
      startTransition(async () => {
        try {
          setStatus("Linked — syncing transactions (can take a minute)…");
          sessionStorage.removeItem(LINK_TOKEN_KEY);
          const result = await exchangePlaidPublicTokenAction(publicToken);
          setStatus(
            `Done — synced +${result.sync.added}. Redirecting to Accounting…`,
          );
          router.replace("/accounting");
          router.refresh();
        } catch (e) {
          setStatus(`Link failed: ${e instanceof Error ? e.message : String(e)}`);
        }
      });
    },
    [router],
  );

  const { open, ready } = usePlaidLink({
    token,
    receivedRedirectUri,
    onSuccess,
  });

  useEffect(() => {
    if (ready && token && receivedRedirectUri) {
      open();
    }
  }, [ready, token, receivedRedirectUri, open]);

  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 p-6">
      <p className="text-sm text-muted-foreground">{status}</p>
      {isPending ? <p className="text-xs text-muted-foreground">Working…</p> : null}
      <a href="/accounting" className="text-xs underline">
        Back to Accounting
      </a>
    </div>
  );
}
