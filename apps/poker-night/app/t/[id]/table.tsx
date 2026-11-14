"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { TableAudio } from "@/lib/audio";
import { api, fmt, loadPref, savedToken, savePref, saveToken, type RoomResponse } from "@/lib/client";
import { AvatarPicker } from "@/components/pickers";
import { AVATARS, blindsUpAt, type Room } from "@/lib/game";
import Dock, { type Send } from "./dock";
import Felt from "./felt";
import Side, { HandRankings } from "./side";

const POLL_MS = 1200;

export default function Table({ roomId }: { roomId: string }) {
  const [room, setRoom] = useState<Room | null>(null);
  const [you, setYou] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [musicOn, setMusicOn] = useState(false);
  const [soundOn, setSoundOn] = useState(true);
  const [coach, setCoach] = useState(true);
  const [volume, setVolume] = useState(0.5);
  const [copied, setCopied] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [lastCards, setLastCards] = useState<{ hand: number; cards: string[] } | null>(null);

  const version = useRef(-1);
  const offset = useRef(0);
  const prev = useRef<Room | null>(null);
  const audio = useRef<TableAudio | null>(null);
  const tokenRef = useRef<string | null>(null);
  const soundRef = useRef(true);

  useEffect(() => {
    const sound = loadPref("sound", true);
    setSoundOn(sound);
    soundRef.current = sound;
    setCoach(loadPref("coach", true));
  }, []);

  const play = useCallback((kind: Parameters<TableAudio["sfx"]>[0]) => {
    if (!soundRef.current) return;
    audio.current ??= new TableAudio();
    audio.current.sfx(kind);
  }, []);

  const cue = useCallback(
    (next: Room, me: string | null) => {
      const old = prev.current;
      prev.current = next;
      if (!old) return;
      const h = next.hand;
      if (h && h.number !== old.hand?.number) play("deal");
      else if (h && old.hand && h.street !== old.hand.street && h.street !== "showdown") play("deal");
      const lastAt = old.log.at(-1)?.at ?? 0;
      const fresh = next.log.filter((l) => l.at > lastAt).map((l) => l.text);
      if (next.lastResult && next.lastResult.at !== old.lastResult?.at) {
        play(next.lastResult.winners.some((w) => w.id === me) ? "bigwin" : "win");
      } else if (fresh.some((t) => /calls|bets|raises|ALL IN|rebuys/.test(t))) play("chip");
      else if (fresh.some((t) => / checks$/.test(t))) play("check");
      else if (fresh.some((t) => / folds$/.test(t))) play("fold");
      const myTurnNow = !!h && h.toAct === me && h.street !== "showdown";
      const myTurnBefore = !!old.hand && old.hand.toAct === me;
      if (myTurnNow && !myTurnBefore) {
        play("turn");
        navigator.vibrate?.(150);
      }
    },
    [play],
  );

  const applyResponse = useCallback(
    (res: RoomResponse) => {
      offset.current = res.now - Date.now();
      setYou(res.you);
      if (res.unchanged || !res.room) return;
      version.current = res.version;
      const r = res.room;
      const mine = res.you ? r.hand?.holes?.[res.you] : undefined;
      if (mine && r.hand) setLastCards({ hand: r.hand.number, cards: mine });
      cue(r, res.you);
      setRoom(r);
    },
    [cue],
  );

  useEffect(() => {
    const t = savedToken(roomId);
    tokenRef.current = t;
    setToken(t);
    let stopped = false;
    async function poll() {
      try {
        const res = await api<RoomResponse>(`/api/rooms/${roomId}?v=${version.current}`, { token: tokenRef.current });
        if (!stopped) applyResponse(res);
      } catch (err) {
        if ((err as Error).message === "Table not found") setMissing(true);
      }
    }
    void poll();
    const timer = setInterval(() => document.visibilityState === "visible" && void poll(), POLL_MS);
    const clock = setInterval(() => setNow(Date.now() + offset.current), 250);
    const onVisible = () => document.visibilityState === "visible" && void poll();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearInterval(timer);
      clearInterval(clock);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [roomId, applyResponse]);

  const myTurn = !!room?.hand && room.hand.toAct === you && you !== null && room.hand.street !== "showdown";
  useEffect(() => {
    document.title = myTurn ? "● Your move — Poker Night" : "Poker Night";
  }, [myTurn]);

  const send: Send = async (body) => {
    setBusy(true);
    setError(null);
    try {
      applyResponse(await api<RoomResponse>(`/api/rooms/${roomId}/action`, { method: "POST", body, token }));
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  async function join(name: string, avatar: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await api<RoomResponse & { token: string }>(`/api/rooms/${roomId}/join`, { method: "POST", body: { name, avatar } });
      saveToken(roomId, res.token);
      tokenRef.current = res.token;
      setToken(res.token);
      applyResponse(res);
      play("chip");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggleMusic() {
    audio.current ??= new TableAudio();
    if (musicOn) audio.current.stop();
    else {
      audio.current.setVolume(volume);
      audio.current.start();
    }
    setMusicOn(!musicOn);
  }

  function toggleSound() {
    const v = !soundOn;
    setSoundOn(v);
    soundRef.current = v;
    savePref("sound", v);
  }

  function toggleCoach() {
    setCoach(!coach);
    savePref("coach", !coach);
  }

  async function share() {
    const url = window.location.href;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Poker Night", text: "Pull up a chair — poker tonight 🃏", url });
        return;
      } catch {
        /* dismissed or unsupported — fall back to copying */
      }
    }
    await navigator.clipboard?.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  if (missing) {
    return (
      <main className="center-screen">
        <div className="card panel narrow">
          <h2>Table not found</h2>
          <p className="muted">This link may be mistyped, or the table expired after 30 days without play.</p>
          <a className="btn primary" href="/">
            Open a new table
          </a>
        </div>
      </main>
    );
  }

  if (!room) {
    return (
      <main className="center-screen">
        <div className="spinner" aria-label="Loading table" />
      </main>
    );
  }

  const me = room.players.find((p) => p.id === you) ?? null;
  const isHost = you === room.hostId;
  const upAt = blindsUpAt(room);
  const left = upAt ? Math.max(0, upAt - now) : null;

  return (
    <div className={`app ${myTurn ? "my-turn" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-suit">♠</span>
          <div className="brand-meta">
            <div className="brand-name">Poker Night</div>
            <div className="chips-row">
              <span className="pill">
                Blinds {fmt(room.settings.smallBlind)}/{fmt(room.settings.bigBlind)}
              </span>
              {left !== null && <span className={`pill ${left < 60_000 ? "warn" : ""}`}>⏱ {left === 0 ? "up next hand" : clock(left)}</span>}
              <span className="pill">{room.settings.dealMode === "app" ? "📱 App deals" : "🃏 Real cards"}</span>
            </div>
          </div>
        </div>
        <div className="top-actions">
          <button className="btn ghost icon" onClick={() => setSheet(true)} title="Hand rankings" aria-label="Hand rankings">
            ?
          </button>
          <button className={`btn ghost icon ${coach ? "lit" : ""}`} onClick={toggleCoach} title={coach ? "Coach on — tips & odds" : "Coach off"} aria-pressed={coach} aria-label="Coach">
            🎓
          </button>
          <button className={`btn ghost icon ${soundOn ? "lit" : ""}`} onClick={toggleSound} title={soundOn ? "Sound effects on" : "Sound effects off"} aria-pressed={soundOn} aria-label="Sound effects">
            {soundOn ? "🔊" : "🔈"}
          </button>
          <div className={`music ${musicOn ? "on" : ""}`}>
            <button className="btn ghost icon" onClick={toggleMusic} aria-pressed={musicOn} title={musicOn ? "Pause music" : "Play lounge music"} aria-label="Music">
              {musicOn ? <Bars /> : "♪"}
            </button>
            {musicOn && (
              <input
                className="volume"
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={volume}
                aria-label="Music volume"
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setVolume(v);
                  audio.current?.setVolume(v);
                }}
              />
            )}
          </div>
          <button className="btn primary invite" onClick={share}>
            {copied ? "Copied ✓" : "Invite"}
          </button>
        </div>
      </header>

      {error && (
        <div className="toast" role="alert" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      <main className="layout">
        <div className="col-main">
          <Felt room={room} you={you} now={now} />
          {me ? (
            <Dock room={room} you={me.id} isHost={isHost} busy={busy} coach={coach} lastCards={lastCards && room.lastResult?.hand === lastCards.hand ? lastCards.cards : null} send={send} />
          ) : (
            <JoinCard room={room} busy={busy} onJoin={join} />
          )}
        </div>
        <aside className="col-side">
          <Side room={room} you={you} isHost={isHost} busy={busy} send={send} />
        </aside>
      </main>

      {sheet && (
        <div className="sheet-backdrop" onClick={() => setSheet(false)}>
          <div className="sheet card" role="dialog" aria-label="Hand rankings" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <h3>Hand rankings</h3>
              <button className="btn ghost icon" onClick={() => setSheet(false)} aria-label="Close">
                ✕
              </button>
            </div>
            <p className="muted small">Best hand at the top. Use any 5 of your 2 cards + the 5 on the table.</p>
            <HandRankings />
          </div>
        </div>
      )}
    </div>
  );
}

function clock(ms: number) {
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Bars() {
  return (
    <span className="bars" aria-hidden>
      <i />
      <i />
      <i />
    </span>
  );
}

function JoinCard({ room, busy, onJoin }: { room: Room; busy: boolean; onJoin: (name: string, avatar: string) => void }) {
  const taken = new Set(room.players.map((p) => p.avatar));
  const [name, setName] = useState("");
  const [avatar, setAvatar] = useState(() => AVATARS.find((a) => !taken.has(a)) ?? AVATARS[0]);
  const host = room.players.find((p) => p.id === room.hostId);
  return (
    <form
      className="card panel join"
      onSubmit={(e) => {
        e.preventDefault();
        onJoin(name, avatar);
      }}
    >
      <h2>Pull up a chair</h2>
      <p className="muted">
        {host?.avatar} {host?.name ?? "The host"} invited you · you&apos;ll start with <b className="cream">{fmt(room.settings.startingChips)}</b> chips ·{" "}
        {room.settings.dealMode === "app" ? "your cards appear on this phone" : "real cards at the table"}
      </p>
      <AvatarPicker value={avatar} onChange={setAvatar} taken={taken} />
      <div className="inline">
        <input name="name" autoComplete="nickname" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" maxLength={20} autoFocus required />
        <button className="btn primary" disabled={busy || !name.trim()}>
          Sit down
        </button>
      </div>
    </form>
  );
}
