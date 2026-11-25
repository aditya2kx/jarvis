import { Moon, Sun, Sunrise, X, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "outline" | "ghost" | "danger" };

export function Button({ variant = "primary", className, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      className={cx(
        "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-[15px] font-medium transition duration-150 enabled:active:scale-[0.97] disabled:opacity-40",
        variant === "primary" && "bg-primary text-primary-foreground shadow-card enabled:hover:brightness-110",
        variant === "outline" && "border border-border bg-card enabled:hover:border-ring enabled:hover:bg-accent",
        variant === "ghost" && "text-muted-foreground enabled:hover:bg-muted enabled:hover:text-foreground",
        variant === "danger" && "border border-border bg-card text-destructive enabled:hover:border-destructive/40 enabled:hover:bg-destructive/5",
        className,
      )}
    />
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

export const inputClass = "min-h-11 w-full rounded-lg border border-border bg-card px-3 text-base transition-colors hover:border-ring";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputClass, props.className)} />;
}

const escapes: (() => void)[] = [];
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") escapes[escapes.length - 1]?.();
});

/** Escape closes only the topmost thing that is open (a photo over a task over the list). */
export function useEscape(onClose: () => void) {
  const latest = useRef(onClose);
  latest.current = onClose;
  useEffect(() => {
    const close = () => latest.current();
    escapes.push(close);
    return () => void escapes.splice(escapes.indexOf(close), 1);
  }, []);
}

/** A bottom sheet on a phone, a centred dialog on anything wider. */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);

  return (
    <div className="fade fixed inset-x-0 top-0 bottom-[calc(var(--vp-gap,0px)*-1)] z-40 flex items-end justify-center bg-foreground/45 backdrop-blur-[2px] sm:items-center sm:p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="sheet flex max-h-[94dvh] w-full max-w-xl flex-col overflow-hidden rounded-t-3xl bg-card shadow-float sm:max-h-[88dvh] sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <span aria-hidden className="mx-auto mt-2 h-1 w-9 shrink-0 rounded-full bg-border sm:hidden" />
        <div className="flex items-start justify-between gap-3 border-b border-border px-5 pb-4 pt-3 sm:pt-4">
          <h2 className="text-lg font-semibold leading-snug">{title}</h2>
          <button aria-label="Close" onClick={onClose} className="-m-2 rounded-full p-2 text-muted-foreground hover:bg-muted">
            <X className="size-5" />
          </button>
        </div>
        <div className="overflow-y-auto px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-4">{children}</div>
      </div>
    </div>
  );
}

/** The round checkbox. The check draws itself when a task is done. */
export function Tick({ done, dim, small }: { done: boolean; dim?: boolean; small?: boolean }) {
  return (
    <span className={cx("tick shrink-0", small ? "size-6" : "size-7", dim && "opacity-60")} data-done={done}>
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M5.5 12.5l4.5 4.5 8.5-10" />
      </svg>
    </span>
  );
}

export type Tone = "dawn" | "day" | "night";

/** A shift's colour and icon follow the time it is due, so a new store's lists need no setup. */
export function shiftTone(due: string): { tone: Tone; Icon: LucideIcon } {
  const hour = Number(due.split(":")[0]);
  if (hour < 11) return { tone: "dawn", Icon: Sunrise };
  if (hour < 17) return { tone: "day", Icon: Sun };
  return { tone: "night", Icon: Moon };
}

/** A short confirmation that goes away by itself. */
export function Toast({ text }: { text: string }) {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(max(1.25rem,env(safe-area-inset-bottom))-var(--vp-gap,0px))] z-50 flex justify-center px-4">
      <p role="status" className="pop rounded-full bg-foreground px-4 py-2.5 text-sm font-medium text-background shadow-float">
        {text}
      </p>
    </div>
  );
}

export function Notice({ tone = "muted", children }: { tone?: "muted" | "error"; children: ReactNode }) {
  return (
    <p
      role={tone === "error" ? "alert" : undefined}
      className={cx("rounded-lg px-3 py-2 text-sm", tone === "error" ? "bg-destructive/10 text-destructive" : "bg-muted text-muted-foreground")}
    >
      {children}
    </p>
  );
}

export const firstName = (name: string | undefined) => (name ?? "").trim().split(/\s+/)[0] ?? "";

const initials = (name: string) => {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
};

const hue = (key: string) => [...key].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);

/** The same person always gets the same colour, so a list can be scanned by colour. */
export function Avatar({ id, name, size = "md" }: { id: string; name: string; size?: "sm" | "md" | "lg" }) {
  const h = hue(id);
  return (
    <span
      aria-hidden
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold",
        size === "sm" && "size-6 text-[10px]",
        size === "md" && "size-8 text-xs",
        size === "lg" && "size-10 text-sm",
      )}
      style={{ background: `oklch(0.92 0.06 ${h})`, color: `oklch(0.36 0.13 ${h})` }}
    >
      {initials(name)}
    </span>
  );
}

/** True on a screen wide enough for the list and one task side by side (iPad landscape, desktop). */
export function useWide(query = "(min-width: 1024px)") {
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return wide;
}

export const time = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }) : "";

export const dayLabel = (date: string) =>
  new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });

export const addDays = (date: string, n: number) => {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
