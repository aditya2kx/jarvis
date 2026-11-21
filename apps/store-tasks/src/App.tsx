import { ArrowLeft, Delete, Store } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, device, isOffline, SHOP, type Bootstrap, type Session, type Staff } from "./api";
import { DayScreen } from "./Day";
import { Manage } from "./Manage";
import { Avatar, cx, Notice } from "./ui";

const BOOT_KEY = "st.bootstrap";
const IDLE_LOCK_MS = 3 * 60 * 1000;
const RENEW_MS = 60 * 60 * 1000; // a shop device stays open for days; its 12-hour session is renewed well before then
const toSession = (own: Bootstrap["own"]): Session | null => (own ? { token: own.session, staff: own.staff } : null);

/** Back from "Sign in with Google": keep the token it carries in the URL fragment, then drop the fragment. */
function takeSignIn(): string {
  const hash = new URLSearchParams(location.hash.slice(1));
  const token = hash.get("signin");
  const error = hash.get("signin_error");
  if (token === null && error === null) return "";
  history.replaceState(null, "", location.pathname + location.search);
  if (token) {
    device.set(token);
    localStorage.removeItem(BOOT_KEY);
  }
  return error ?? "";
}

export function App() {
  const [signInError] = useState(takeSignIn);
  const [boot, setBoot] = useState<Bootstrap | null>(() => JSON.parse(localStorage.getItem(BOOT_KEY) ?? "null"));
  const [session, setSession] = useState<Session | null>(null);
  const [managing, setManaging] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [error, setError] = useState(signInError);

  const loadBoot = useCallback(() => {
    api
      .bootstrap()
      .then((b) => {
        if (b.device) device.set(b.device);
        // A person's PIN-free session is used once and never kept; the shop's is the device's own.
        localStorage.setItem(BOOT_KEY, JSON.stringify(b.account.shop ? b : { ...b, own: null }));
        setBoot(b);
        setError("");
        setSignedOut(false);
        setSession((cur) => (cur && cur.staff.id !== SHOP ? cur : toSession(b.own) ?? cur));
      })
      .catch((e) => {
        if (isOffline(e)) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          // Never signed in is not an error; a refused account or a changed shop code is.
          const explain = e.status === 403 || device.get() !== null;
          localStorage.removeItem(BOOT_KEY);
          device.set(null);
          setBoot(null);
          setSession(null);
          setSignedOut(true);
          setError((cur) => (explain ? e.message : cur));
          return;
        }
        setError((e as Error).message);
      });
  }, []);

  useEffect(loadBoot, [loadBoot]);

  const shop = Boolean(boot?.account.shop);
  useEffect(() => {
    if (!shop) return;
    const timer = window.setInterval(loadBoot, RENEW_MS);
    return () => window.clearInterval(timer);
  }, [shop, loadBoot]);

  const shared = !boot?.own;
  // On the shop device "lock" hands the screen back to the shop; elsewhere it asks for a name again.
  const lock = useCallback(() => {
    setSession(shop ? toSession(boot?.own ?? null) : null);
    setManaging(false);
    setSigningIn(false);
  }, [shop, boot]);

  // A shared tablet: sign the person out after a few idle minutes so the next tick is not theirs.
  const idle = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!session || (!shared && !(shop && session.staff.id !== SHOP))) return;
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

  if (signedOut) return <Welcome error={error} onEnrolled={loadBoot} />;
  if (!boot) {
    return <Centered>{error ? <Notice tone="error">{error}</Notice> : <p className="text-muted-foreground">Loading…</p>}</Centered>;
  }
  if (signingIn || !session) {
    return (
      <SignIn
        boot={boot}
        managersOnly={shop}
        onCancel={shop && session ? () => setSigningIn(false) : undefined}
        onSignedIn={(s) => {
          setSigningIn(false);
          setSession(s);
        }}
      />
    );
  }
  if (managing) return <Manage boot={boot} session={session} onBack={() => { setManaging(false); loadBoot(); }} />;
  const onShop = shop && session.staff.id === SHOP;
  return (
    <DayScreen
      boot={boot}
      session={session}
      onLock={onShop ? () => setSigningIn(true) : shared || shop ? lock : null}
      onManage={() => setManaging(true)}
    />
  );
}

/** The one link's front door: a person signs in with Google; the shop iPad enters the store's code, once. */
function Welcome({ error, onEnrolled }: { error: string; onEnrolled: () => void }) {
  const [shop, setShop] = useState(false);
  if (shop) {
    return (
      <CodePad
        title="Shop iPad"
        prompt="Enter the shop code"
        length={6}
        onBack={() => setShop(false)}
        submit={async (code) => {
          device.set((await api.enrollShop(code)).device);
          onEnrolled();
        }}
      />
    );
  }
  return (
    <div className="flex min-h-full items-center justify-center bg-primary p-6 text-primary-foreground">
      <div className="w-full max-w-xs space-y-8 text-center">
        <div className="rise">
          <span role="img" aria-label="Palmetto Superfoods" className="brand-mark brand-word mx-auto block w-60" />
          <p className="mt-4 text-[15px] text-primary-foreground/80">Sign in to see today's tasks</p>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="rise flex flex-col gap-3" style={{ "--i": 3 } as React.CSSProperties}>
          <a
            href="/signin"
            className="inline-flex min-h-12 items-center justify-center gap-3 rounded-full bg-card px-5 text-[15px] font-semibold text-foreground shadow-float transition hover:brightness-95 active:scale-[0.97]"
          >
            <GoogleMark />
            Sign in with Google
          </a>
          <button
            onClick={() => setShop(true)}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-full px-4 text-[15px] font-medium text-primary-foreground/85 transition hover:bg-white/10 hover:text-primary-foreground active:scale-[0.97]"
          >
            <Store className="size-4" />
            Set up this iPad for the shop
          </button>
        </div>
      </div>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" className="size-5" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-full items-center justify-center p-6">{children}</div>;
}

interface SignInProps {
  boot: Bootstrap;
  /** On the shop device crew never sign in, so only managers are listed. */
  managersOnly: boolean;
  onCancel?: () => void;
  onSignedIn: (s: Session) => void;
}

function SignIn({ boot, managersOnly, onCancel, onSignedIn }: SignInProps) {
  const [who, setWho] = useState<Staff | null>(null);
  const people = managersOnly ? boot.staff.filter((s) => s.role === "manager") : boot.staff;
  const onShift = managersOnly ? [] : people.filter((s) => boot.on_today.includes(s.id));
  const others = people.filter((s) => !onShift.includes(s));

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
              className="flex min-h-16 items-center gap-3 rounded-xl border border-border bg-card px-4 text-left text-lg font-medium shadow-card transition hover:border-ring hover:bg-accent active:scale-[0.98]"
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
        {onCancel && (
          <button onClick={onCancel} className="-ml-2 mb-2 flex items-center gap-1 rounded-lg p-2 text-muted-foreground active:bg-muted">
            <ArrowLeft className="size-5" />
            Back to the list
          </button>
        )}
        <p className="text-sm text-muted-foreground">{boot.store.name}</p>
        <h1 className="text-2xl font-extrabold tracking-tight">{managersOnly ? "Manager sign-in" : "Who's working?"}</h1>
      </header>
      {group("On shift today", onShift)}
      {group(managersOnly ? "Managers" : onShift.length ? "Everyone else" : "Team", others)}
    </div>
  );
}

function PinPad({ who, onBack, onSignedIn }: { who: Staff; onBack: () => void; onSignedIn: (s: Session) => void }) {
  return (
    <CodePad
      title={who.name}
      prompt={who.has_pin ? "Enter your PIN" : "Ask a manager to set your PIN"}
      length={who.role === "manager" ? 6 : 4}
      onBack={onBack}
      submit={async (pin) => {
        const r = await api.signIn(who.id, pin);
        onSignedIn({ token: r.session, staff: r.staff });
      }}
    />
  );
}

interface CodePadProps {
  title: string;
  prompt: string;
  length: number;
  onBack?: () => void;
  /** Called once all digits are in; a thrown error is shown and the digits cleared. */
  submit: (code: string) => Promise<void>;
}

function CodePad({ title, prompt, length, onBack, submit }: CodePadProps) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (pin.length !== length || busy) return;
    setBusy(true);
    // Busy stays set on success: the screen is about to go away, and must not send the code twice.
    submit(pin).catch((e) => {
      setError(isOffline(e) ? "No connection. Sign-in needs Wi-Fi." : (e as Error).message);
      setPin("");
      setBusy(false);
    });
  }, [pin, length, busy, submit]);

  const press = (d: string) => {
    setError("");
    setPin((p) => (p.length < length ? p + d : p));
  };

  return (
    <Centered>
      <div className="w-full max-w-xs space-y-6 text-center">
        <div>
          <span aria-hidden className="brand-mark brand-palm mx-auto mb-3 block h-11 text-primary" />
          <h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>
          <p className="mt-1 text-muted-foreground">{prompt}</p>
        </div>
        <div className="flex justify-center gap-3" aria-label={`${pin.length} of ${length} digits entered`}>
          {Array.from({ length }, (_, i) => (
            <span key={i} className={cx("size-4 rounded-full border-2 border-primary transition", i < pin.length && "scale-110 bg-primary")} />
          ))}
        </div>
        <div className="min-h-9">{error && <Notice tone="error">{error}</Notice>}</div>
        <div className="grid grid-cols-3 gap-3">
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <Key key={d} onClick={() => press(d)}>{d}</Key>
          ))}
          {onBack ? <Key onClick={onBack} quiet><ArrowLeft className="size-6" aria-label="Back" /></Key> : <span />}
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
