"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, saveToken } from "@/lib/client";

export default function Home() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [chips, setChips] = useState(1000);
  const [sb, setSb] = useState(10);
  const [bb, setBb] = useState(20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ roomId: string; token: string }>("/api/rooms", {
        method: "POST",
        body: { name, startingChips: chips, smallBlind: sb, bigBlind: bb },
      });
      saveToken(res.roomId, res.token);
      router.push(`/t/${res.roomId}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="landing">
      <div className="hero">
        <div className="suits" aria-hidden>
          <span>♠</span>
          <span className="red">♥</span>
          <span>♣</span>
          <span className="red">♦</span>
        </div>
        <h1>Poker Night</h1>
        <p className="lede">Deal real cards. We keep the chips, blinds, turns and score — on everyone&apos;s phone.</p>
      </div>

      <form className="card panel" onSubmit={create}>
        <h2>Open a table</h2>
        <label className="field">
          <span>Your name</span>
          <input name="name" autoComplete="nickname" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Adi" maxLength={20} autoFocus required />
        </label>
        <div className="row3">
          <label className="field">
            <span>Starting chips</span>
            <input type="number" inputMode="numeric" name="startingChips" min={10} value={chips} onChange={(e) => setChips(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Small blind</span>
            <input type="number" inputMode="numeric" name="smallBlind" min={1} value={sb} onChange={(e) => setSb(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Big blind</span>
            <input type="number" inputMode="numeric" name="bigBlind" min={1} value={bb} onChange={(e) => setBb(Number(e.target.value))} />
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <button className="btn primary big" disabled={busy || !name.trim()}>
          {busy ? "Setting the table…" : "Create table & get link"}
        </button>
        <p className="muted small">You&apos;ll be the host. Share the link with your friends so they can take a seat.</p>
      </form>

      <ol className="how">
        <li>
          <b>Share the link.</b> Friends open it and pick a name.
        </li>
        <li>
          <b>Deal a hand.</b> Blinds post and the dealer button moves automatically.
        </li>
        <li>
          <b>Bet on your phone.</b> Fold, check, call or raise when it&apos;s your turn.
        </li>
        <li>
          <b>Flip and tap the winner.</b> Pots (and side pots) pay out instantly.
        </li>
      </ol>
    </main>
  );
}
