"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { AvatarPicker, DealModePicker } from "@/components/pickers";
import { api, saveToken } from "@/lib/client";
import { AVATARS, type DealMode } from "@/lib/game";
import PlayingCard from "@/components/playing-card";

const PRESETS = [
  { label: "Casual", chips: 1000, sb: 10, bb: 20, level: 0 },
  { label: "Deep stack", chips: 5000, sb: 25, bb: 50, level: 0 },
  { label: "Turbo", chips: 1500, sb: 10, bb: 20, level: 10 },
];

export default function Home() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [avatar, setAvatar] = useState(AVATARS[0]);
  const [mode, setMode] = useState<DealMode>("physical");
  const [chips, setChips] = useState(1000);
  const [sb, setSb] = useState(10);
  const [bb, setBb] = useState(20);
  const [level, setLevel] = useState(0);
  const [custom, setCustom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preset = PRESETS.find((p) => p.chips === chips && p.sb === sb && p.bb === bb && p.level === level);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ roomId: string; token: string }>("/api/rooms", {
        method: "POST",
        body: { name, avatar, dealMode: mode, startingChips: chips, smallBlind: sb, bigBlind: bb, blindLevelMinutes: level },
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
        <div className="fan" aria-hidden>
          <PlayingCard card="As" size="lg" />
          <PlayingCard card="Kh" size="lg" />
          <PlayingCard card="Qd" size="lg" />
          <PlayingCard card="Jc" size="lg" />
        </div>
        <h1>Poker Night</h1>
        <p className="lede">Your home game, upgraded. Chips, blinds, turns and bragging rights on every phone — deal real cards or let the app deal.</p>
      </div>

      <form className="card panel create" onSubmit={create}>
        <h2>Open a table</h2>
        <label className="field">
          <span>Your name</span>
          <input name="name" autoComplete="nickname" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Adi" maxLength={20} autoFocus required />
        </label>
        <div className="field">
          <span>Your avatar</span>
          <AvatarPicker value={avatar} onChange={setAvatar} />
        </div>
        <div className="field">
          <span>Who deals?</span>
          <DealModePicker value={mode} onChange={setMode} />
        </div>
        <div className="field">
          <span>Game style</span>
          <div className="presets">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                className={`chip-btn ${preset === p && !custom ? "sel" : ""}`}
                onClick={() => {
                  setChips(p.chips);
                  setSb(p.sb);
                  setBb(p.bb);
                  setLevel(p.level);
                  setCustom(false);
                }}
              >
                {p.label}
              </button>
            ))}
            <button type="button" className={`chip-btn ${custom || !preset ? "sel" : ""}`} onClick={() => setCustom(true)}>
              Custom
            </button>
          </div>
          <small className="muted">
            {chips.toLocaleString()} chips · blinds {sb}/{bb}
            {level ? ` · blinds double every ${level} min` : " · fixed blinds"}
          </small>
        </div>
        {custom && (
          <div className="row4">
            <label className="field">
              <span>Chips</span>
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
            <label className="field">
              <span>Blinds up</span>
              <select name="blindLevel" value={level} onChange={(e) => setLevel(Number(e.target.value))}>
                {[0, 10, 15, 20, 30].map((m) => (
                  <option key={m} value={m}>
                    {m ? `Every ${m}m` : "Never"}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <button className="btn primary big" disabled={busy || !name.trim()}>
          {busy ? "Shuffling…" : "Create table & get invite link"}
        </button>
      </form>

      <div className="features">
        <div>
          <span>🎓</span>
          <b>Coach mode</b>
          <small>Plain-English tips, win chance and pot odds — great for first-timers.</small>
        </div>
        <div>
          <span>🏆</span>
          <b>Side pots & splits</b>
          <small>All-ins handled exactly. No more chip arguments.</small>
        </div>
        <div>
          <span>📈</span>
          <b>Live leaderboard</b>
          <small>Net results, streaks, awards and stats for the sharks.</small>
        </div>
        <div>
          <span>🎶</span>
          <b>Lounge vibes</b>
          <small>Soft music, chip sounds and emoji reactions.</small>
        </div>
      </div>
    </main>
  );
}
