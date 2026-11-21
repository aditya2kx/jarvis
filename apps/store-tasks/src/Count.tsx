import { useEffect, useState } from "react";
import { api, isOffline, type Bootstrap, type Count, type Session } from "./api";
import { AddPhotos, PhotoGrid } from "./Photos";
import { Button, cx, dayLabel, Field, firstName, Input, inputClass, Notice, Sheet, time } from "./ui";

type Draft = Pick<Count, "items" | "notes" | "tips">;

const EMPTY: Draft = { items: {}, notes: { shift: "", low: "", out: "" }, tips: "" };
const draftKey = (date: string) => `st.count.${date}`;

interface Props {
  boot: Bootstrap;
  session: Session;
  date: string;
  names: Map<string, string>;
  /** A day done in ClickUp: the count is shown, not edited. */
  readOnly: boolean;
  onClose: () => void;
}

export function CountForm({ boot, session, date, names, readOnly, onClose }: Props) {
  const [draft, setDraft] = useState<Draft>(() => (readOnly ? null : JSON.parse(localStorage.getItem(draftKey(date)) ?? "null")) ?? EMPTY);
  const [saved, setSaved] = useState<Count | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // What is typed survives a locked screen or a closed tab until it is sent.
  useEffect(() => {
    if (!readOnly) localStorage.setItem(draftKey(date), JSON.stringify(draft));
  }, [draft, date, readOnly]);

  useEffect(() => {
    api
      .count(session.token, date)
      .then(({ count }) => {
        if (!count) return;
        setSaved(count);
        if (count.submitted_at && (readOnly || !localStorage.getItem(draftKey(date)) || Object.keys(draft.items).length === 0)) {
          setDraft({ items: count.items, notes: count.notes, tips: count.tips ?? "" });
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per day
  }, [session.token, date]);

  const filled = boot.store.count_items.filter((i) => draft.items[i.id]?.trim()).length;
  const photos = saved?.photos ?? [];

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

  const addPhoto = async (file: Blob) => {
    setError("");
    try {
      setSaved((await api.countPhoto(session.token, date, file)).count);
    } catch (e) {
      setError(isOffline(e) ? "No connection. The photo was not saved — try again when Wi-Fi is back." : (e as Error).message);
    }
  };

  const note = (key: keyof Draft["notes"], label: string) => (
    <Field label={label}>
      <textarea
        rows={2}
        disabled={readOnly}
        value={draft.notes[key]}
        onChange={(e) => setDraft((d) => ({ ...d, notes: { ...d.notes, [key]: e.target.value } }))}
        className={cx(inputClass, "py-2")}
      />
    </Field>
  );

  return (
    <Sheet title={`Closing count · ${dayLabel(date)}`} onClose={onClose}>
      <div className="space-y-4">
        {saved?.submitted_at && (
          <Notice>
            Sent{saved.submitted_by && ` by ${firstName(names.get(saved.submitted_by)) || "someone"}`} at {time(saved.submitted_at)}.
            {!readOnly && " Sending again replaces it; the earlier one is kept in history."}
          </Notice>
        )}
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          {boot.store.count_items.map((i) => (
            <Field key={i.id} label={i.name}>
              <Input
                inputMode={i.kind === "number" ? "decimal" : "text"}
                autoComplete="off"
                autoCapitalize="off"
                disabled={readOnly}
                value={draft.items[i.id] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, items: { ...d.items, [i.id]: e.target.value } }))}
              />
            </Field>
          ))}
        </div>
        <Field label="Tips ($)">
          <Input inputMode="decimal" disabled={readOnly} value={draft.tips ?? ""} onChange={(e) => setDraft((d) => ({ ...d, tips: e.target.value }))} />
        </Field>
        {note("low", "Running low")}
        {note("out", "Out of")}
        {note("shift", "Shift notes")}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-muted-foreground">Photos (deep clean, freezer, anything to flag)</h3>
          <PhotoGrid token={session.token} photos={photos} names={names} />
          {!photos.length && readOnly && <p className="text-muted-foreground">No photos.</p>}
          {!readOnly && <AddPhotos label="Add photo" onFile={addPhoto} />}
        </section>
        {error && <Notice tone="error">{error}</Notice>}
        {!readOnly && (
          <Button onClick={submit} disabled={busy || filled === 0} className="w-full">
            {busy ? "Sending…" : `Send count (${filled}/${boot.store.count_items.length} filled)`}
          </Button>
        )}
      </div>
    </Sheet>
  );
}
