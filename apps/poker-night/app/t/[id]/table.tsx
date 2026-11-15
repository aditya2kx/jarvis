"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { TableAudio } from "@/lib/audio";
import { BUILD } from "@/lib/build";
import {
  api,
  fmt,
  forgetToken,
  loadPref,
  safeToReload,
  saveClaim,
  savedClaim,
  savedHostKey,
  savedToken,
  savePref,
  saveToken,
  type PendingClaim,
  type RoomResponse,
  type VoicePresence,
} from "@/lib/client";
import { AvatarPicker } from "@/components/pickers";
import { AVATARS, blindsUpAt, canVouch, type Room } from "@/lib/game";
import { VOICE_IDLE, VoiceMesh, type VoiceStatus } from "@/lib/voice-client";
import Dock, { type Send } from "./dock";
import Felt from "./felt";
import Side, { HandRankings } from "./side";

const POLL_MS = 1200;
const RESUME_MUSIC = "poker-night:resume-music";
const RELOADED_FOR = "poker-night:reloaded-for";

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
  const [claim, setClaim] = useState<PendingClaim | null>(null);
  const [voice, setVoice] = useState<VoiceStatus>(VOICE_IDLE);
  const [roster, setRoster] = useState<VoicePresence[]>([]);
  const [voiceBusy, setVoiceBusy] = useState(false);

  const version = useRef(-1);
  const offset = useRef(0);
  const prev = useRef<Room | null>(null);
  const audio = useRef<TableAudio | null>(null);
  const tokenRef = useRef<string | null>(null);
  const soundRef = useRef(true);
  const live = useRef({ myTurn: false, busy: false, music: false });
  const shell = useRef<HTMLDivElement | null>(null);
  const topbar = useRef<HTMLElement | null>(null);
  const mesh = useRef<VoiceMesh | null>(null);

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
      const heardAt = old.chat?.at(-1)?.at ?? 0;
      if (next.chat?.some((m) => m.at > heardAt && m.playerId !== me)) play("chat");
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
      if (res.voice) {
        const next = res.voice;
        setRoster((old) => (JSON.stringify(old) === JSON.stringify(next) ? old : next));
      }
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
    setClaim(savedClaim(roomId));
    let stopped = false;
    async function poll() {
      try {
        const res = await api<RoomResponse>(`/api/rooms/${roomId}?v=${version.current}`, { token: tokenRef.current });
        if (stopped) return;
        applyResponse(res);
        // Once per build ever seen: a cached page or old and new servers answering in turn mid-rollout
        // must not cause a reload loop.
        const seen = (sessionStorage.getItem(RELOADED_FOR) ?? "").split(",");
        const h = res.room?.hand;
        const myTurnNow = h ? h.toAct === res.you && res.you !== null && h.street !== "showdown" : live.current.myTurn;
        if (res.build && res.build !== BUILD && !seen.includes(res.build) && safeToReload({ ...live.current, myTurn: myTurnNow })) {
          sessionStorage.setItem(RELOADED_FOR, [...seen, BUILD, res.build].filter(Boolean).join(","));
          if (live.current.music) sessionStorage.setItem(RESUME_MUSIC, "1");
          window.location.reload();
        }
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
  live.current = { myTurn, busy, music: musicOn };

  // A seat request is over once it leaves the room: either the seat is ours now, or it was turned down / expired.
  useEffect(() => {
    if (!claim || !room || room.claims?.some((c) => c.id === claim.claimId)) return;
    setClaim(null);
    saveClaim(roomId, null);
    if (you) {
      play("chip");
      return;
    }
    forgetToken(roomId);
    tokenRef.current = null;
    setToken(null);
    setError("Nobody let you in — ask someone at the table to tap “Let them in”, then try again");
  }, [claim, room, you, roomId, play]);

  // The play column is sized to the viewport minus the top bar, whose height changes with wrapping pills.
  const hasRoom = room !== null;
  useEffect(() => {
    const bar = topbar.current;
    if (!bar) return;
    const sync = () => shell.current?.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(bar);
    return () => ro.disconnect();
  }, [hasRoom]);

  useEffect(() => {
    if (sessionStorage.getItem(RESUME_MUSIC) !== "1") return;
    sessionStorage.removeItem(RESUME_MUSIC);
    // Browsers only allow audio after a gesture, so pick the music back up on the first tap.
    const resume = () => {
      audio.current ??= new TableAudio();
      audio.current.start();
      setMusicOn(true);
    };
    window.addEventListener("pointerdown", resume, { once: true });
    return () => window.removeEventListener("pointerdown", resume);
  }, []);

  // Leave voice when this phone loses its seat or the page goes away.
  const seatedId = room?.players.some((p) => p.id === you) ? you : null;
  useEffect(() => {
    if (!seatedId) return;
    return () => {
      void mesh.current?.leave();
      mesh.current = null;
    };
  }, [seatedId]);

  useEffect(() => {
    if (!voice.joined) return;
    const unlock = () => mesh.current?.unlock();
    window.addEventListener("pointerdown", unlock);
    return () => window.removeEventListener("pointerdown", unlock);
  }, [voice.joined]);

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

  async function askForSeat(playerId: string) {
    setBusy(true);
    setError(null);
    try {
      const hostKey = savedHostKey(roomId);
      const res = await api<RoomResponse & { token: string; claimId: string }>(`/api/rooms/${roomId}/claim`, {
        method: "POST",
        body: { playerId, ...(hostKey ? { hostKey } : {}) },
      });
      saveToken(roomId, res.token);
      tokenRef.current = res.token;
      setToken(res.token);
      if (res.you) {
        // Host key (or already settled) — seated immediately.
        saveClaim(roomId, null);
        setClaim(null);
      } else {
        const pending = { claimId: res.claimId, playerId };
        saveClaim(roomId, pending);
        setClaim(pending);
      }
      applyResponse(res);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function cancelSeatRequest() {
    forgetToken(roomId);
    tokenRef.current = null;
    setToken(null);
    setClaim(null);
  }

  async function tapMic() {
    if (voiceBusy || !seatedId || !token) return;
    const live = mesh.current;
    if (live && voice.joined) {
      if (voice.listenOnly) setError("Mic is blocked — allow it in your browser settings, then leave and rejoin voice");
      else void live.setMuted(!voice.muted);
      return;
    }
    setVoiceBusy(true);
    const m = new VoiceMesh(roomId, seatedId, token, setVoice);
    try {
      const listenOnly = await m.join();
      mesh.current = m;
      if (listenOnly) setError("Mic is blocked — you can listen. Allow the mic in your browser settings to talk");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setVoiceBusy(false);
    }
  }

  async function leaveVoice() {
    const m = mesh.current;
    mesh.current = null;
    await m?.leave();
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
    <div ref={shell} className={`app ${myTurn ? "my-turn" : ""}`}>
      <header ref={topbar} className="topbar">
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
          {me && <VoiceControl voice={voice} others={roster.filter((v) => v.id !== me.id).length} busy={voiceBusy} onMic={tapMic} onLeave={leaveVoice} />}
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
        <div className={`col-main ${me ? "fit" : ""}`}>
          <div className="felt-box">
            <Felt room={room} you={you} now={now} voice={{ roster, speaking: voice.speaking }} />
          </div>
          {me && <SeatRequests room={room} you={me.id} busy={busy} send={send} />}
          {me ? (
            <Dock room={room} you={me.id} isHost={isHost} busy={busy} coach={coach} lastCards={lastCards && room.lastResult?.hand === lastCards.hand ? lastCards.cards : null} send={send} />
          ) : claim ? (
            <WaitingForSeat room={room} claim={claim} onCancel={cancelSeatRequest} />
          ) : (
            <JoinCard room={room} busy={busy} onJoin={join} onClaim={askForSeat} />
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

function VoiceControl({ voice, others, busy, onMic, onLeave }: { voice: VoiceStatus; others: number; busy: boolean; onMic: () => void; onLeave: () => void }) {
  const talking = voice.joined && !voice.muted;
  const title = !voice.joined
    ? `Join voice chat${others ? ` · ${others} talking` : ""} — use headphones if you're sitting together`
    : voice.listenOnly
      ? "Listening only — mic is blocked"
      : voice.muted
        ? "Muted — tap to talk"
        : "Mic on — tap to mute";
  return (
    <div className={`voice ${voice.joined ? "on" : ""}`}>
      <button
        className={`btn ghost icon mic ${talking ? "lit live" : ""}`}
        onClick={onMic}
        disabled={busy}
        aria-pressed={talking}
        aria-label={voice.joined ? (voice.muted ? "Unmute" : "Mute") : "Join voice chat"}
        title={title}
      >
        {voice.joined && voice.muted ? "🔇" : "🎙️"}
        {!voice.joined && others > 0 && <span className="voice-count">{others}</span>}
      </button>
      {voice.joined && (
        <button className="btn ghost icon voice-leave" onClick={onLeave} aria-label="Leave voice chat" title="Leave voice chat">
          ✕
        </button>
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

function SeatRequests({ room, you, busy, send }: { room: Room; you: string; busy: boolean; send: Send }) {
  const claims = room.claims ?? [];
  if (!claims.length) return null;
  return (
    <div className="seat-requests">
      {claims.map((c) => {
        const p = room.players.find((x) => x.id === c.playerId);
        if (!p) return null;
        return (
          <div key={c.id} className="card seat-request" role="alert">
            <span className="seat-request-text">
              🙋{" "}
              {c.playerId === you
                ? "Someone is asking for your seat — not you? Tap No."
                : `${p.avatar} ${p.name} wants their seat back (refreshed or new phone)`}
            </span>
            <span className="seat-request-actions">
              {canVouch(room, you, c) && (
                <button className="btn primary small-btn" disabled={busy} onClick={() => send({ kind: "seat", claimId: c.id, allow: true })}>
                  Let them in
                </button>
              )}
              <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "seat", claimId: c.id, allow: false })}>
                No
              </button>
            </span>
          </div>
        );
      })}
    </div>
  );
}

function WaitingForSeat({ room, claim, onCancel }: { room: Room; claim: PendingClaim; onCancel: () => void }) {
  const p = room.players.find((x) => x.id === claim.playerId);
  return (
    <div className="card panel join">
      <h2>
        {p?.avatar} Getting {p?.name ?? "your"}&apos;s seat back…
      </h2>
      <p className="muted">
        Ask anyone at the table to tap <b className="cream">Let them in</b>. Your chips, cards and seat come back with it. Fastest: open the
        table in the browser you first used — your seat comes straight back.
      </p>
      <div className="inline">
        <div className="spinner tiny-spin" aria-label="Waiting for the table" />
        <button className="btn ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function JoinCard({
  room,
  busy,
  onJoin,
  onClaim,
}: {
  room: Room;
  busy: boolean;
  onJoin: (name: string, avatar: string) => void;
  onClaim: (playerId: string) => void;
}) {
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
      {room.players.length > 0 && (
        <div className="rejoin">
          <div className="rejoin-title">Already playing? Tap your name to get your seat back</div>
          <div className="rejoin-list">
            {room.players.map((p) => (
              <button key={p.id} type="button" className="btn ghost small-btn" disabled={busy} onClick={() => onClaim(p.id)}>
                {p.avatar} {p.name}
                {p.id === room.hostId ? " · host" : ""}
              </button>
            ))}
          </div>
        </div>
      )}
      <h2>Pull up a chair</h2>
      <p className="muted">
        {host?.avatar} {host?.name ?? "The host"} invited you · you&apos;ll start with <b className="cream">{fmt(room.settings.startingChips)}</b> chips ·{" "}
        {room.settings.dealMode === "app" ? "your cards appear on this phone" : "real cards at the table"}
      </p>
      <AvatarPicker value={avatar} onChange={setAvatar} taken={taken} />
      <div className="inline">
        <input name="name" autoComplete="nickname" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" maxLength={20} required />
        <button className="btn primary" disabled={busy || !name.trim()}>
          Sit down
        </button>
      </div>
    </form>
  );
}
