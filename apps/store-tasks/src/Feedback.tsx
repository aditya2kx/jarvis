import { Check, ChevronDown, ImageIcon, MessageSquareWarning, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, isOffline, type Feedback, type FeedbackKind, type Session } from "./api";
import { AddPhotos, PhotoGrid } from "./Photos";
import { loadQueue } from "./queue";
import { recentSteps, snapshot, track } from "./steps";
import { Button, cx, dayLabel, inputClass, Notice, Sheet, time, Toast } from "./ui";

const KINDS: { id: FeedbackKind; label: string; prompt: string }[] = [
  { id: "problem", label: "Something's wrong", prompt: "What happened, and what did you expect?" },
  { id: "idea", label: "I have an idea", prompt: "What would make this easier or faster?" },
];
const TOAST_MS = 2600;

/** From a store (a staff session) or from a project (the member's Google account). */
type Target = { session: Session } | { project: string };

/** The one way to report a problem or an idea, from any screen. */
export function FeedbackButton(target: Target) {
  const [open, setOpen] = useState(false);
  const [thanks, setThanks] = useState(false);

  useEffect(() => {
    if (!thanks) return;
    const timer = window.setTimeout(() => setThanks(false), TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [thanks]);

  return (
    <>
      <button
        aria-label="Report a problem or an idea"
        onClick={() => {
          track("action", "open feedback");
          setOpen(true);
        }}
        className="flex min-h-10 items-center gap-2 rounded-full border border-border bg-card px-3 text-[15px] font-medium text-foreground transition hover:bg-muted active:scale-95"
      >
        <MessageSquareWarning className="size-4 text-primary" />
        <span className="sm:hidden">Problem?</span>
        <span className="hidden sm:inline">Report a problem</span>
      </button>
      {open && (
        <FeedbackSheet
          target={target}
          onClose={() => setOpen(false)}
          onSent={() => {
            setOpen(false);
            setThanks(true);
          }}
        />
      )}
      {thanks && <Toast text="Sent. Thank you, we will look at it." />}
    </>
  );
}

function FeedbackSheet({ target, onClose, onSent }: { target: Target; onClose: () => void; onSent: () => void }) {
  const [kind, setKind] = useState<FeedbackKind>("problem");
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const prompt = KINDS.find((k) => k.id === kind)!.prompt;

  const send = async () => {
    setSending(true);
    setError("");
    try {
      const context = { ...snapshot(), queued: loadQueue().length };
      const body = { kind, text: text.trim(), context, events: recentSteps() };
      await ("project" in target ? api.projectFeedback(target.project, body, photo ?? undefined) : api.sendFeedback(target.session.token, body, photo ?? undefined));
      onSent();
    } catch (e) {
      setError(isOffline(e) ? "No connection. Keep this open and send again when you are back online." : (e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Sheet title="Report a problem or an idea" onClose={onClose}>
      <div className="space-y-4">
        <div role="radiogroup" aria-label="Kind" className="grid grid-cols-2 gap-1 rounded-xl bg-muted p-1">
          {KINDS.map((k) => (
            <button
              key={k.id}
              role="radio"
              aria-checked={k.id === kind}
              onClick={() => setKind(k.id)}
              className={cx("min-h-11 rounded-lg text-[15px] font-medium transition", k.id === kind ? "bg-card shadow-sm" : "text-muted-foreground")}
            >
              {k.label}
            </button>
          ))}
        </div>
        <textarea
          autoFocus
          aria-label={prompt}
          placeholder={prompt}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className={cx(inputClass, "min-h-32 resize-y py-2.5 leading-relaxed")}
        />
        {photo ? (
          <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-[15px]">
            <span className="flex items-center gap-2">
              <ImageIcon className="size-4 text-muted-foreground" />
              Photo added
            </span>
            <button aria-label="Remove the photo" onClick={() => setPhoto(null)} className="-m-1 rounded-full p-2 text-muted-foreground hover:bg-muted">
              <X className="size-4" />
            </button>
          </div>
        ) : (
          <AddPhotos label="Add a screenshot or photo" className="w-full" onFile={async (f) => setPhoto(f)} />
        )}
        <p className="text-sm text-muted-foreground">We also send what was on screen and your last few steps in the app, so it can be fixed quickly.</p>
        {error && <Notice tone="error">{error}</Notice>}
        <Button className="min-h-12 w-full text-base" disabled={sending || (!text.trim() && !photo)} onClick={send}>
          {sending ? "Sending…" : "Send"}
        </Button>
      </div>
    </Sheet>
  );
}

/** Manage → Feedback: newest first, with what was on screen and the steps that led there. */
export function FeedbackList({ token }: { token: string }) {
  const [items, setItems] = useState<Feedback[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api
      .feedback(token)
      .then((r) => setItems(r.feedback))
      .catch((e: Error) => setError(e.message || "Could not load. Check the connection."));
  }, [token]);
  useEffect(load, [load]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!items) return <p className="text-muted-foreground">Loading…</p>;
  if (!items.length) return <Notice>Nothing yet. Anyone can send a problem or an idea with the Feedback button on the list.</Notice>;
  const toggle = async (f: Feedback) => {
    const next = await api.closeFeedback(token, f.id, f.status !== "done");
    setItems((list) => list?.map((x) => (x.id === f.id ? next : x)) ?? null);
  };

  return (
    <ul className="space-y-3">
      {items.map((f) => (
        <FeedbackItem key={f.id} token={token} f={f} onToggle={() => toggle(f)} />
      ))}
    </ul>
  );
}

function FeedbackItem({ token, f, onToggle }: { token: string; f: Feedback; onToggle: () => void }) {
  const [details, setDetails] = useState(false);
  const ctx = f.context as Record<string, string | undefined>;
  const sent = new Date(f.at).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "America/Chicago" });
  const who = [...new Set([f.staff_name, f.device].filter(Boolean))].join(" on ");
  const shift = ctx.shift && ctx.shift.charAt(0).toUpperCase() + ctx.shift.slice(1).replace(/_/g, " ");
  const list = ctx.date && [shift, ctx.date && `list for ${dayLabel(ctx.date)}`].filter(Boolean).join(" ");
  const where = [ctx.screen === "List" ? list : ctx.screen, ctx.task && `“${ctx.task}”`].filter(Boolean).join(" · ");
  const done = f.status === "done";
  return (
    <li className={cx("space-y-3 rounded-xl border border-border bg-card p-4 shadow-card", done && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <span className={cx("inline-flex rounded-full px-2 py-0.5 text-[13px] font-medium", f.kind === "problem" ? "bg-destructive/10 text-destructive" : "bg-accent text-accent-foreground")}>
            {f.kind === "problem" ? "Something's wrong" : "Idea"}
          </span>
          <p className="whitespace-pre-wrap text-[16px] leading-snug">{f.text || "(photo only)"}</p>
          <p className="text-sm text-muted-foreground">
            {[who, `${sent} ${time(f.at)}`].filter(Boolean).join(" · ")}
            {where && <><br />On: {where}</>}
          </p>
        </div>
        <Button variant="outline" className="shrink-0" onClick={onToggle}>
          <Check className="size-4" />
          {done ? "Reopen" : "Done"}
        </Button>
      </div>
      {f.photos.length > 0 && <PhotoGrid token={token} photos={f.photos} names={new Map()} />}
      <button onClick={() => setDetails((d) => !d)} className="flex items-center gap-1 text-sm font-medium text-primary">
        <ChevronDown className={cx("size-4 transition-transform", details && "rotate-180")} />
        {details ? "Hide details" : `Details · ${f.events.length} steps`}
      </button>
      {details && (
        <div className="space-y-2 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-[12px] leading-relaxed">
          <p className="break-all">{Object.entries(f.context).map(([k, v]) => `${k}=${String(v)}`).join("  ")}</p>
          <ol className="space-y-0.5">
            {f.events.map((e, i) => (
              <li key={i} className={cx(e.kind === "error" || (e.status !== undefined && (e.status === 0 || e.status >= 400)) ? "text-destructive" : "")}>
                {time(e.at)} {e.kind} {e.what}
                {e.status !== undefined && ` → ${e.status || "no connection"}`}
                {e.ms !== undefined && ` ${e.ms} ms`}
              </li>
            ))}
          </ol>
        </div>
      )}
    </li>
  );
}
