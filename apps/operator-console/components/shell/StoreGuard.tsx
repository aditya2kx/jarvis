"use client";

import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { readStoreCookie, storeDisplayName, type StoreId } from "@/lib/config/stores";

type Rendered = { store: StoreId; refresh: () => void };

const RenderedStore = createContext<Rendered | null>(null);

/** The store this page was rendered for (null outside the console shell, e.g. unit tests). */
export function useRenderedStore(): Rendered | null {
  return useContext(RenderedStore);
}

/**
 * Null when the tab still shows the selected store; otherwise the message to show instead
 * of saving. The store is a cookie shared by every tab, so a tab left on Austin while
 * another tab switched to Houston would otherwise write Austin's edit into Houston.
 */
export function staleStoreMessage(rendered: StoreId | null): string | null {
  const selected = readStoreCookie();
  if (rendered === null || selected === null || selected === rendered) return null;
  return `This tab was showing ${storeDisplayName(rendered)}, but the console is now on ${storeDisplayName(selected)}. The page has been reloaded — nothing was saved.`;
}

/** Re-renders a tab for the selected store as soon as it is looked at again. */
export function StoreGuard({ store, children }: { store: StoreId; children: ReactNode }) {
  const router = useRouter();
  useEffect(() => {
    const check = () => {
      if (document.visibilityState === "visible" && staleStoreMessage(store)) router.refresh();
    };
    document.addEventListener("visibilitychange", check);
    window.addEventListener("focus", check);
    return () => {
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
    };
  }, [store, router]);
  const value = useMemo(() => ({ store, refresh: () => router.refresh() }), [store, router]);
  return <RenderedStore.Provider value={value}>{children}</RenderedStore.Provider>;
}
