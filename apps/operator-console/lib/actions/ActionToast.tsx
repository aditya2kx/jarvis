"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ToastAction = { label: string; onClick: () => void };

type Toast = { id: number; text: string; tone: "info" | "error"; action?: ToastAction };

type ToastApi = {
  push: (text: string, tone?: "info" | "error", action?: ToastAction) => void;
};

const ToastCtx = createContext<ToastApi | null>(null);

let _seq = 0;

export function ActionToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => setItems((prev) => prev.filter((t) => t.id !== id)), []);

  const push = useCallback(
    (text: string, tone: "info" | "error" = "info", action?: ToastAction) => {
      const id = ++_seq;
      setItems((prev) => [...prev, { id, text, tone, action }]);
      // An undo needs a little longer to reach than a plain confirmation.
      window.setTimeout(() => dismiss(id), action ? 10000 : 6000);
    },
    [dismiss],
  );

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed right-4 bottom-[calc(var(--tabbar-h)+env(safe-area-inset-bottom)+0.75rem)] left-4 z-50 flex flex-col gap-2 sm:left-auto sm:max-w-sm md:bottom-4"
        data-testid="action-toast-root"
        aria-live="polite"
      >
        {items.map((t) => (
          <div
            key={t.id}
            className={
              t.tone === "error"
                ? "rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive shadow"
                : "pointer-events-auto flex items-center gap-3 rounded-md border bg-background px-3 py-2 text-sm text-foreground shadow"
            }
          >
            <span className="min-w-0 flex-1">{t.text}</span>
            {t.action ? (
              <button
                type="button"
                className="-my-1 shrink-0 rounded-md px-2 py-1 text-sm font-medium text-primary transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => {
                  dismiss(t.id);
                  t.action!.onClick();
                }}
              >
                {t.action.label}
              </button>
            ) : null}
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
