import { ArrowLeft, Delete } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, isOffline, type Bootstrap, type Session, type Staff } from "./api";
import { DayScreen } from "./Day";
import { Manage } from "./Manage";
import { Avatar, cx, Notice } from "./ui";

const BOOT_KEY = "st.bootstrap";
const IDLE_LOCK_MS = 3 * 60 * 1000;

export function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(() => JSON.parse(localStorage.getItem(BOOT_KEY) ?? "null"));
  const [session, setSession] = useState<Session | null>(null);
  const [managing, setManaging] = useState(false);
  const [error, setError] = useState("");

  const loadBoot = useCallback(() => {
    api
      .bootstrap()
      .then((b) => {
        // The PIN-free session is used once and never kept on the device.
        localStorage.setItem(BOOT_KEY, JSON.stringify({ ...b, own: null }));
        setBoot(b);
        setError("");
        if (b.own) setSession((cur) => cur ?? { token: b.own!.session, staff: b.own!.staff });
      })
      .catch((e) => {
        if (isOffline(e)) return;
        if (e instanceof ApiError && e.status === 403) {
          localStorage.removeItem(BOOT_KEY);
          setBoot(null);
        }
        setError((e as Error).message);
      });
  }, []);

  useEffect(loadBoot, [loadBoot]);

  const shared = !boot?.own;
  const lock = useCallback(() => {
    setSession(null);
    setManaging(false);
  }, []);

  // A shared tablet: sign the person out after a few idle minutes so the next tick is not theirs.
  const idle = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!session || !shared) return;
    const reset = () => {
      window.clearTimeout(idle.current);
      idle.current = window.setTimeout(lock, IDLE_LOCK_MS);
    };
    reset();
    window.addEventListener("pointerdown", reset);
    window.addEventListener("keydown", reset);
    return () => {
      window.clearTimeout(idle.current);
      window.removeEventListener("pointerdown", reset);
      window.removeEventListener("keydown", reset);
    };
  }, [session, shared, lock]);

  if (!boot) {
    return (
      <Centered>
        {error ? (
          <div className="max-w-sm space-y-3 text-center">
            <Notice tone="error">{error}</Notice>
            <a href="/?gcp-iap-mode=CLEAR_LOGIN_COOKIE" className="inline-block text-sm text-muted-foreground underline">
              Use a different Google account
            </a>
          </div>
        ) : (
          <p className="text-muted-foreground">Loading…</p>
        )}
      </Centered>
    );
  }
  if (!session) return <SignIn boot={boot} onSignedIn={setSession} />;
  if (managing) return <Manage boot={boot} session={session} onBack={() => { setManaging(false); loadBoot(); }} />;
  return <DayScreen boot={boot} session={session} onLock={shared ? lock : null} onManage={() => setManaging(true)} />;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-full items-center justify-center p-6">{children}</div>;
}

function SignIn({ boot, onSignedIn }: { boot: Bootstrap; onSignedIn: (s: Session) => void }) {
  const [who, setWho] = useState<Staff | null>(null);
  const onShift = boot.staff.filter((s) => boot.on_today.includes(s.id));
  const others = boot.staff.filter((s) => !boot.on_today.includes(s.id));

  if (who) return <PinPad who={who} onBack={() => setWho(null)} onSignedIn={onSignedIn} />;

  const group = (title: string, people: Staff[]) =>
    people.length > 0 && (
      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">{title}</h2>
        <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          {people.map((s) => (
            <button
              key={s.id}
              onClick={() => setWho(s)}
              className="flex min-h-16 items-center gap-3 rounded-xl border border-border bg-card px-4 text-left text-lg font-medium transition hover:bg-muted active:scale-[0.98]"
            >
              <Avatar id={s.id} name={s.name} size="lg" />
              <span className="min-w-0 truncate">{s.name}</span>
            </button>
          ))}
        </div>
      </section>
    );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 pt-[max(1.5rem,env(safe-area-inset-top))]">
      <header>
        <p className="text-sm text-muted-foreground">{boot.store.name}</p>
        <h1 className="text-2xl font-semibold">Who's working?</h1>
      </header>
      {group("On shift today", onShift)}
      {group(onShift.length ? "Everyone else" : "Team", others)}
    </div>
  );
}

function PinPad({ who, onBack, onSignedIn }: { who: Staff; onBack: () => void; onSignedIn: (s: Session) => void }) {
  const length = who.role === "manager" ? 6 : 4;
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (pin.length !== length || busy) return;
    setBusy(true);
    api
      .signIn(who.id, pin)
      .then((r) => onSignedIn({ token: r.session, staff: r.staff }))
      .catch((e) => {
        setError(isOffline(e) ? "No connection. Sign-in needs Wi-Fi." : (e as Error).message);
        setPin("");
      })
      .finally(() => setBusy(false));
  }, [pin, length, busy, who.id, onSignedIn]);

  const press = (d: string) => {
    setError("");
    setPin((p) => (p.length < length ? p + d : p));
  };

  return (
    <Centered>
      <div className="w-full max-w-xs space-y-6 text-center">
        <div>
          <h1 className="text-2xl font-semibold">{who.name}</h1>
          <p className="mt-1 text-muted-foreground">{who.has_pin ? "Enter your PIN" : "Ask a manager to set your PIN"}</p>
        </div>
        <div className="flex justify-center gap-3" aria-label={`${pin.length} of ${length} digits entered`}>
          {Array.from({ length }, (_, i) => (
            <span key={i} className={cx("size-4 rounded-full border-2 border-foreground", i < pin.length && "bg-foreground")} />
          ))}
        </div>
        <div className="min-h-9">{error && <Notice tone="error">{error}</Notice>}</div>
        <div className="grid grid-cols-3 gap-3">
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <Key key={d} onClick={() => press(d)}>{d}</Key>
          ))}
          <Key onClick={onBack} quiet><ArrowLeft className="size-6" aria-label="Back" /></Key>
          <Key onClick={() => press("0")}>0</Key>
          <Key onClick={() => setPin((p) => p.slice(0, -1))} quiet><Delete className="size-6" aria-label="Delete" /></Key>
        </div>
      </div>
    </Centered>
  );
}

function Key({ children, onClick, quiet }: { children: React.ReactNode; onClick: () => void; quiet?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={cx(
        "flex h-18 items-center justify-center rounded-2xl text-2xl font-medium transition active:scale-95 active:bg-muted",
        quiet ? "text-muted-foreground" : "border border-border bg-card",
      )}
    >
      {children}
    </button>
  );
}
