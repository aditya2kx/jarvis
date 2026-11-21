import { useEffect, useState } from "react";
import { api, isOffline, type Bootstrap, type Count, type Session } from "./api";
import { Button, cx, dayLabel, Field, Input, inputClass, Notice, Sheet, time } from "./ui";

type Draft = Pick<Count, "items" | "notes" | "tips">;

const EMPTY: Draft = { items: {}, notes: { shift: "", low: "", out: "" }, tips: "" };
const draftKey = (date: string) => `st.count.${date}`;

interface Props {
  boot: Bootstrap;
  session: Session;
  date: string;
  onClose: () => void;
}

export function CountForm({ boot, session, date, onClose }: Props) {
  const [draft, setDraft] = useState<Draft>(() => JSON.parse(localStorage.getItem(draftKey(date)) ?? "null") ?? EMPTY);
  const [sent, setSent] = useState<Count | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // What is typed survives a locked screen or a closed tab until it is sent.
  useEffect(() => localStorage.setItem(draftKey(date), JSON.stringify(draft)), [draft, date]);

  useEffect(() => {
    api
      .count(session.token, date)
      .then(({ count }) => {
        if (!count) return;
        setSent(count);
        if (!localStorage.getItem(draftKey(date)) || Object.keys(draft.items).length === 0) {
          setDraft({ items: count.items, notes: count.notes, tips: count.tips ?? "" });
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per day
  }, [session.token, date]);

  const filled = boot.store.count_items.filter((i) => draft.items[i.id]?.trim()).length;

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api.submitCount(session.token, date, draft);
      localStorage.removeItem(draftKey(date));
      onClose();
    } catch (e) {
      setError(isOffline(e) ? "No connection. Your count is saved on this device — send it when Wi-Fi is back." : (e as Error).message);
      setBusy(false);
    }
  };

  const note = (key: keyof Draft["notes"], label: string) => (
    <Field label={label}>
      <textarea
        rows={2}
        value={draft.notes[key]}
        onChange={(e) => setDraft((d) => ({ ...d, notes: { ...d.notes, [key]: e.target.value } }))}
        className={cx(inputClass, "py-2")}
      />
    </Field>
  );

  return (
    <Sheet title={`Closing count · ${dayLabel(date)}`} onClose={onClose}>
      <div className="space-y-4">
        {sent && <Notice>Sent at {time(sent.submitted_at)}. Sending again replaces it; the earlier one is kept in history.</Notice>}
        <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
          {boot.store.count_items.map((i) => (
            <Field key={i.id} label={i.name}>
              <Input
                inputMode={i.kind === "number" ? "decimal" : "text"}
                autoComplete="off"
                autoCapitalize="off"
                value={draft.items[i.id] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, items: { ...d.items, [i.id]: e.target.value } }))}
              />
            </Field>
          ))}
        </div>
        <Field label="Tips ($)">
          <Input inputMode="decimal" value={draft.tips ?? ""} onChange={(e) => setDraft((d) => ({ ...d, tips: e.target.value }))} />
        </Field>
        {note("low", "Running low")}
        {note("out", "Out of")}
        {note("shift", "Shift notes")}
        {error && <Notice tone="error">{error}</Notice>}
        <Button onClick={submit} disabled={busy || filled === 0} className="w-full">
          {busy ? "Sending…" : `Send count (${filled}/${boot.store.count_items.length} filled)`}
        </Button>
      </div>
    </Sheet>
  );
}
