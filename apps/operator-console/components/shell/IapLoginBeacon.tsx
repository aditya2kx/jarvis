"use client";

import { useEffect } from "react";
import { CANONICAL_CONSOLE_HOST } from "@/lib/iap/hosts";

const SESSION_KEY = "oc_iap_beacon_v1";

/**
 * One-shot post-login success beacon (Issue #194). Fires after IAP has already
 * admitted the browser — captures host / UA / referrer for login diagnostics.
 */
export function IapLoginBeacon() {
  useEffect(() => {
    try {
      if (typeof window === "undefined") return;
      if (sessionStorage.getItem(SESSION_KEY)) return;
      sessionStorage.setItem(SESSION_KEY, "1");
      const host = window.location.host;
      void fetch("/api/iap-beacon", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          host,
          ua: navigator.userAgent,
          referrer: document.referrer || null,
          path: window.location.pathname,
          dual_host: host !== CANONICAL_CONSOLE_HOST,
        }),
        keepalive: true,
      }).catch(() => {
        /* never block the shell on telemetry */
      });
    } catch {
      /* ignore */
    }
  }, []);

  return null;
}
