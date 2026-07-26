"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type Toast = { id: number; text: string; tone: "info" | "error" };

type ToastApi = {
  push: (text: string, tone?: "info" | "error") => void;
};

const ToastCtx = createContext<ToastApi | null>(null);

let _seq = 0;

export function ActionToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);

  const push = useCallback((text: string, tone: "info" | "error" = "info") => {
    const id = ++_seq;
    setItems((prev) => [...prev, { id, text, tone }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  }, []);

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2"
        data-testid="action-toast-root"
        aria-live="polite"
      >
        {items.map((t) => (
          <div
            key={t.id}
            className={
              t.tone === "error"
                ? "rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive shadow"
                : "rounded-md border bg-background px-3 py-2 text-sm text-foreground shadow"
            }
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useActionToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) {
    return { push: () => undefined };
  }
  return ctx;
}
