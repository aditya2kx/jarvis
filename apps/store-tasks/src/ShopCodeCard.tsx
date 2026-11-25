import { useCallback, useEffect, useState } from "react";
import { api, device } from "./api";
import { shopCodeButtonLabel, shopCodeStatus } from "./shopCodeUi";
import { Button, Field, Input, Notice } from "./ui";

function useLoad<T>(load: () => Promise<T>): [T | null, string, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const run = useCallback(() => {
    load()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e: Error) => setError(e.message || "Could not load. Check the connection."));
  }, [load]);
  useEffect(run, [run]);
  return [data, error, run];
}

/** Manage → Sign-in: every store this manager opens, with plaintext shop codes. */
export function ShopCodeCard({ token }: { token: string }) {
  const load = useCallback(() => api.shopCodes(token), [token]);
  const [data, error, reload] = useLoad(load);
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [busyStore, setBusyStore] = useState<string | null>(null);
  const [failed, setFailed] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <p className="text-muted-foreground">Loading…</p>;

  const run = async (store: string, code?: string) => {
    setBusyStore(store);
    setFailed("");
    setFlash(null);
    try {
      const r = await api.setShopCode(token, { store, code });
      if (r.device) device.set(r.device);
      setCustom((c) => ({ ...c, [store]: "" }));
      setFlash(store);
      reload();
    } catch (err) {
      setFailed((err as Error).message);
    } finally {
      setBusyStore(null);
    }
  };

  const copy = async (store: string, code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(store);
      setTimeout(() => setCopied((c) => (c === store ? null : c)), 1500);
    } catch {
      setFailed("Could not copy — select the code and copy it yourself.");
    }
  };

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Shop devices</h2>
      <p className="text-muted-foreground">
        Each store has one 6-digit code. On the iPad open{" "}
        {data.url ? <span className="font-medium text-foreground break-all">{data.url}</span> : "the Store Tasks link"}, tap
        “Set up this iPad for the shop”, and enter that store’s code once. Crew use the shop as-is (no Google); managers sign in with their PIN to manage. Issuing or changing a code signs that store’s shop devices out.
      </p>
      <ul className="space-y-3">
        {data.stores.map((row) => {
          const digits = custom[row.store!] ?? "";
          const busy = busyStore === row.store;
          return (
            <li key={row.store} className="rounded-xl border border-border bg-card p-4 space-y-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-semibold">{row.name ?? row.store}</h3>
                {row.updated_at && (
                  <p className="text-sm text-muted-foreground">
                    {row.needs_rotate ? "Set before digits were kept" : "Updated"}{" "}
                    {new Date(row.updated_at).toLocaleString()}
                  </p>
                )}
              </div>
              {shopCodeStatus(row) === "shown" ? (
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-mono text-2xl tracking-[0.35em] text-foreground">{row.code}</p>
                  <Button type="button" variant="outline" className="sm:w-auto" onClick={() => copy(row.store!, row.code!)}>
                    {copied === row.store ? "Copied" : "Copy"}
                  </Button>
                </div>
              ) : shopCodeStatus(row) === "needs_rotate" ? (
                <Notice>Code is set but the digits weren’t stored. Issue a new one to see it (shop iPads will need the new code).</Notice>
              ) : (
                <p className="text-sm text-muted-foreground">No shop code yet.</p>
              )}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(row.store!, digits.length === 6 ? digits : undefined);
                }}
                className="flex flex-col gap-2 sm:flex-row sm:items-end"
              >
                <Field label="Custom code (optional)">
                  <Input
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={6}
                    placeholder="Leave blank to issue"
                    value={digits}
                    onChange={(e) => setCustom((c) => ({ ...c, [row.store!]: e.target.value.replace(/\D/g, "") }))}
                  />
                </Field>
                <Button type="submit" disabled={busy || (digits.length > 0 && digits.length !== 6)} className="sm:w-48">
                  {shopCodeButtonLabel(row, digits.length, busy)}
                </Button>
              </form>
              {flash === row.store && <Notice>Saved. Share the code above with that store’s iPad.</Notice>}
            </li>
          );
        })}
      </ul>
      {data.stores.length === 0 && <Notice>You’re not a manager on any store yet.</Notice>}
      {failed && <Notice tone="error">{failed}</Notice>}
    </section>
  );
}
