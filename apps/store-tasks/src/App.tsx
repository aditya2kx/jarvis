import { ArrowLeft, Delete } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, isOffline, SHOP, shopDevice, type Bootstrap, type Session, type Staff } from "./api";
import { DayScreen } from "./Day";
import { Manage } from "./Manage";
import { Avatar, cx, Notice } from "./ui";

const BOOT_KEY = "st.bootstrap";
const IDLE_LOCK_MS = 3 * 60 * 1000;
const RENEW_MS = 60 * 60 * 1000; // a shop device stays open for days; its 12-hour session is renewed well before then
const toSession = (own: Bootstrap["own"]): Session | null => (own ? { token: own.session, staff: own.staff } : null);

export function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(() => JSON.parse(localStorage.getItem(BOOT_KEY) ?? "null"));
  const [session, setSession] = useState<Session | null>(null);
  const [managing, setManaging] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState("");

  const loadBoot = useCallback(() => {
    api
      .bootstrap()
      .then((b) => {
        if (b.device) shopDevice.set(b.device);
        // A person's PIN-free session is used once and never kept; the shop's is the device's own.
        localStorage.setItem(BOOT_KEY, JSON.stringify(b.account.shop ? b : { ...b, own: null }));
        setBoot(b);
        setError("");
        setNeedCode(false);
        setSession((cur) => (cur && cur.staff.id !== SHOP ? cur : toSession(b.own) ?? cur));
      })
      .catch((e) => {
        if (isOffline(e)) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          localStorage.removeItem(BOOT_KEY);
          setBoot(null);
          setSession(null);
          // No Google account here: this is a shop device, signed out or never signed in.
          if (e.status === 401) {
            shopDevice.set(null);
            setNeedCode(true);
          }
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

  if (needCode) return <ShopCode onEnrolled={loadBoot} />;
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

/** A shop device has no Google account: the store's 6-digit code signs it in, once. */
function ShopCode({ onEnrolled }: { onEnrolled: () => void }) {
  return (
    <CodePad
      title="Store Tasks"
      prompt="Enter the shop code"
      length={6}
      submit={async (code) => {
        shopDevice.set((await api.enrollShop(code)).device);
        onEnrolled();
      }}
    />
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
        <h1 className="text-2xl font-semibold">{managersOnly ? "Manager sign-in" : "Who's working?"}</h1>
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
          <h1 className="text-2xl font-semibold">{title}</h1>
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
