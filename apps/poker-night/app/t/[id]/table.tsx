"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, savedToken, saveToken, type RoomResponse } from "@/lib/client";
import { pots, potTotal, type Hand, type Player, type Room, type Street } from "@/lib/game";
import { LoungeMusic } from "@/lib/music";

const POLL_MS = 1500;

const STREET_LABEL: Record<Street, string> = {
  preflop: "Pre-flop",
  flop: "Flop",
  turn: "Turn",
  river: "River",
  showdown: "Showdown",
};

const BOARD_CARDS: Record<Street, number> = { preflop: 0, flop: 3, turn: 4, river: 5, showdown: 5 };

function dealHint(h: Hand, allInRunout: boolean) {
  switch (h.street) {
    case "preflop":
      return "Deal two cards to each player";
    case "flop":
      return "Deal the flop — three cards face up";
    case "turn":
      return "Deal the turn — one more card";
    case "river":
      return "Deal the river — the last card";
    case "showdown":
      return allInRunout ? "All in! Run out the rest of the board, then pick who won" : "Flip your cards and pick who won";
  }
}

const fmt = (n: number) => n.toLocaleString("en-US");

type Body = Record<string, unknown>;

export default function Table({ roomId }: { roomId: string }) {
  const [room, setRoom] = useState<Room | null>(null);
  const [you, setYou] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [musicOn, setMusicOn] = useState(false);
  const [volume, setVolume] = useState(0.5);
  const [copied, setCopied] = useState(false);
  const version = useRef(-1);
  const lastLogAt = useRef(0);
  const music = useRef<LoungeMusic | null>(null);
  const tokenRef = useRef<string | null>(null);

  const applyResponse = useCallback((res: RoomResponse) => {
    setYou(res.you);
    if (res.unchanged || !res.room) return;
    version.current = res.version;
    setRoom(res.room);
  }, []);

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
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void poll();
    }, POLL_MS);
    const onVisible = () => document.visibilityState === "visible" && void poll();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [roomId, applyResponse]);

  useEffect(() => {
    if (!room) return;
    const fresh = room.log.filter((l) => l.at > lastLogAt.current);
    if (lastLogAt.current > 0 && musicOn && fresh.some((l) => /calls|bets|raises|wins/.test(l.text))) music.current?.chip();
    lastLogAt.current = room.log.at(-1)?.at ?? lastLogAt.current;
  }, [room, musicOn]);

  const myTurn = !!room?.hand && room.hand.toAct === you && you !== null;
  useEffect(() => {
    document.title = myTurn ? "● Your turn — Poker Night" : "Poker Night";
    if (myTurn) navigator.vibrate?.(120);
  }, [myTurn]);

  async function send(body: Body) {
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
  }

  async function join(name: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await api<RoomResponse & { token: string }>(`/api/rooms/${roomId}/join`, { method: "POST", body: { name } });
      saveToken(roomId, res.token);
      tokenRef.current = res.token;
      setToken(res.token);
      applyResponse(res);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggleMusic() {
    music.current ??= new LoungeMusic();
    if (musicOn) music.current.stop();
    else {
      music.current.setVolume(volume);
      music.current.start();
    }
    setMusicOn(!musicOn);
  }

  async function share() {
    const url = window.location.href;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Poker Night", text: "Take a seat at my poker table", url });
        return;
      } catch {
        /* fall through to copy when the share sheet is dismissed or unsupported */
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
          <p className="muted">This link may be mistyped, or the table expired after 30 days of no play.</p>
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-suit">♠</span>
          <div>
            <div className="brand-name">Poker Night</div>
            <div className="muted small">
              Blinds {fmt(room.settings.smallBlind)}/{fmt(room.settings.bigBlind)} · {room.handCount} hand{room.handCount === 1 ? "" : "s"} played
            </div>
          </div>
        </div>
        <div className="top-actions">
          <button className="btn ghost" onClick={share}>
            {copied ? "Link copied ✓" : "Share link"}
          </button>
          <div className={`music ${musicOn ? "on" : ""}`}>
            <button className="btn ghost icon" onClick={toggleMusic} aria-pressed={musicOn} title={musicOn ? "Pause music" : "Play lounge music"}>
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
                  music.current?.setVolume(v);
                }}
              />
            )}
          </div>
        </div>
      </header>

      {error && (
        <div className="toast" role="alert" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      <main className="layout">
        <div className="col-main">
          <Felt room={room} />
          <Seats room={room} you={you} />
          {!me ? (
            <JoinCard room={room} busy={busy} onJoin={join} />
          ) : room.hand ? (
            room.hand.street === "showdown" ? (
              <Showdown room={room} hand={room.hand} busy={busy} onAward={(winners) => send({ kind: "showdown", winners })} />
            ) : (
              <ActionBar room={room} hand={room.hand} you={me.id} isHost={isHost} busy={busy} send={send} />
            )
          ) : (
            <NextHand room={room} busy={busy} onDeal={() => send({ kind: "start" })} />
          )}
        </div>
        <aside className="col-side">
          <Scoreboard room={room} you={you} busy={busy} send={send} />
          {isHost && <HostTools room={room} busy={busy} send={send} />}
          <Log room={room} />
        </aside>
      </main>
    </div>
  );
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

function Felt({ room }: { room: Room }) {
  const h = room.hand;
  const total = h ? potTotal(h) : 0;
  const allInRunout = !!h && h.street === "showdown" && hadAllIn(room, h);
  return (
    <section className="felt">
      <div className="felt-inner">
        {h ? (
          <>
            <div className="street-pill">
              Hand #{h.number} · {STREET_LABEL[h.street]}
            </div>
            <div className="board" aria-label={`${BOARD_CARDS[h.street]} community cards`}>
              {Array.from({ length: 5 }, (_, i) => (
                <div key={i} className={`slot ${i < BOARD_CARDS[h.street] ? "dealt" : ""}`} />
              ))}
            </div>
            <div className="pot">
              <ChipStack />
              <span>Pot {fmt(total)}</span>
            </div>
            <p className="hint">{dealHint(h, allInRunout)}</p>
          </>
        ) : (
          <>
            <div className="street-pill">Between hands</div>
            <p className="result">{room.lastResult ?? "Waiting for the first deal"}</p>
          </>
        )}
      </div>
    </section>
  );
}

function hadAllIn(room: Room, h: Hand) {
  return h.order.some((id) => !h.folded.includes(id) && room.players.find((p) => p.id === id)?.chips === 0);
}

function ChipStack() {
  return (
    <span className="chipstack" aria-hidden>
      <i />
      <i />
      <i />
    </span>
  );
}

function Seats({ room, you }: { room: Room; you: string | null }) {
  const h = room.hand;
  return (
    <section className="seats">
      {room.players.map((p) => {
        const inHand = !!h?.order.includes(p.id);
        const folded = !!h?.folded.includes(p.id);
        const turn = h?.toAct === p.id;
        const bet = h?.bets[p.id] ?? 0;
        const status = !h
          ? p.sittingOut
            ? "Sitting out"
            : p.chips === 0
              ? "Busted"
              : null
          : !inHand
            ? "Waiting"
            : folded
              ? "Folded"
              : p.chips === 0
                ? "All in"
                : turn
                  ? "To act"
                  : null;
        return (
          <div key={p.id} className={`seat ${turn ? "turn" : ""} ${folded || (h && !inHand) ? "dim" : ""} ${p.id === you ? "me" : ""}`}>
            <div className="seat-top">
              <span className="seat-name">{p.name}</span>
              <span className="badges">
                {h?.dealerId === p.id && <span className="badge dealer">D</span>}
                {h?.sbId === p.id && <span className="badge">SB</span>}
                {h?.bbId === p.id && <span className="badge">BB</span>}
                {room.hostId === p.id && <span className="badge host" title="Host">★</span>}
              </span>
            </div>
            <div className="seat-chips">
              {fmt(p.chips)}
              {p.id === you && <span className="you">you</span>}
            </div>
            <div className="seat-foot">
              {bet > 0 ? <span className="bet">bet {fmt(bet)}</span> : <span />}
              {status && <span className={`status ${status === "To act" ? "go" : ""}`}>{status}</span>}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function JoinCard({ room, busy, onJoin }: { room: Room; busy: boolean; onJoin: (name: string) => void }) {
  const [name, setName] = useState("");
  return (
    <form
      className="card panel"
      onSubmit={(e) => {
        e.preventDefault();
        onJoin(name);
      }}
    >
      <h2>Take a seat</h2>
      <p className="muted">
        {room.players.find((p) => p.id === room.hostId)?.name ?? "The host"} invited you. You&apos;ll start with {fmt(room.settings.startingChips)} chips.
      </p>
      <div className="inline">
        <input name="name" autoComplete="nickname" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" maxLength={20} autoFocus required />
        <button className="btn primary" disabled={busy || !name.trim()}>
          Join table
        </button>
      </div>
    </form>
  );
}

function NextHand({ room, busy, onDeal }: { room: Room; busy: boolean; onDeal: () => void }) {
  const ready = room.players.filter((p) => !p.sittingOut && p.chips > 0);
  const ids = room.players.map((p) => p.id);
  const prev = room.lastDealerId;
  let dealer = ready[0];
  if (prev && ids.includes(prev)) {
    const start = ids.indexOf(prev);
    for (let i = 1; i <= ids.length; i++) {
      const cand = room.players[(start + i) % ids.length];
      if (ready.includes(cand)) {
        dealer = cand;
        break;
      }
    }
  }
  return (
    <section className="card panel action-panel">
      {ready.length < 2 ? (
        <p className="muted">Waiting for at least two players with chips. Share the link to invite friends.</p>
      ) : (
        <>
          <p className="muted">
            <b className="cream">{dealer?.name}</b> has the dealer button next. Blinds {fmt(room.settings.smallBlind)}/{fmt(room.settings.bigBlind)} post automatically.
          </p>
          <button className="btn primary big" disabled={busy} onClick={onDeal}>
            Deal next hand
          </button>
        </>
      )}
    </section>
  );
}

function ActionBar({ room, hand, you, isHost, busy, send }: { room: Room; hand: Hand; you: string; isHost: boolean; busy: boolean; send: (b: Body) => Promise<boolean> }) {
  const actorId = hand.toAct;
  const actor = room.players.find((p) => p.id === actorId);
  const mine = actorId === you;
  const [proxy, setProxy] = useState(false);
  const controls = mine || (isHost && proxy);

  const myBet = actor ? (hand.bets[actor.id] ?? 0) : 0;
  const owe = Math.max(0, hand.currentBet - myBet);
  const maxTo = actor ? myBet + actor.chips : 0;
  const minTo = Math.min(maxTo, hand.currentBet + hand.minRaise);
  const pot = potTotal(hand);
  const [raiseTo, setRaiseTo] = useState(minTo);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    setRaiseTo(minTo);
    setEditing(false);
  }, [actorId, hand.currentBet, minTo, hand.street]);

  if (!actor) return null;

  const move = (m: Body) => send({ kind: "act", playerId: actor.id, ...m }).then((ok) => ok && setEditing(false));
  const canRaise = maxTo > hand.currentBet;
  const clamp = (n: number) => Math.max(minTo, Math.min(maxTo, Math.round(n)));
  const presets: [string, number][] = [
    ["Min", minTo],
    ["½ pot", clamp(hand.currentBet + (pot + owe) / 2)],
    ["Pot", clamp(hand.currentBet + pot + owe)],
  ];

  if (!controls) {
    return (
      <section className="card panel action-panel waiting">
        <div className="waiting-row">
          <span className="pulse" />
          <span>
            Waiting on <b className="cream">{actor.name}</b>
            {owe > 0 ? ` — ${fmt(owe)} to call` : ""}
          </span>
        </div>
        {isHost && (
          <button className="btn ghost small-btn" onClick={() => setProxy(true)}>
            Act for {actor.name}
          </button>
        )}
      </section>
    );
  }

  return (
    <section className={`card panel action-panel ${mine ? "your-turn" : ""}`}>
      <div className="action-head">
        <h2>{mine ? "Your turn" : `Acting for ${actor.name}`}</h2>
        <span className="muted">
          Stack {fmt(actor.chips)}
          {owe > 0 && <> · {fmt(owe)} to call</>}
        </span>
        {!mine && (
          <button className="btn ghost small-btn" onClick={() => setProxy(false)}>
            Done
          </button>
        )}
      </div>

      {editing && canRaise ? (
        <div className="raise">
          <div className="presets">
            {presets.map(([label, v]) => (
              <button key={label} className={`chip-btn ${raiseTo === v ? "sel" : ""}`} onClick={() => setRaiseTo(v)} disabled={busy}>
                {label}
              </button>
            ))}
            <button className={`chip-btn ${raiseTo === maxTo ? "sel" : ""}`} onClick={() => setRaiseTo(maxTo)} disabled={busy}>
              All in
            </button>
          </div>
          <input
            type="range"
            className="slider"
            min={minTo}
            max={maxTo}
            step={room.settings.smallBlind}
            value={raiseTo}
            onChange={(e) => setRaiseTo(Number(e.target.value))}
            aria-label="Raise amount"
          />
          <div className="inline">
            <input type="number" inputMode="numeric" value={raiseTo} min={minTo} max={maxTo} onChange={(e) => setRaiseTo(Number(e.target.value))} aria-label="Raise to" />
            <button className="btn ghost" onClick={() => setEditing(false)} disabled={busy}>
              Back
            </button>
            <button className="btn primary" disabled={busy} onClick={() => move(raiseTo >= maxTo ? { move: "allin" } : { move: "raise", to: raiseTo })}>
              {raiseTo >= maxTo ? `All in ${fmt(maxTo)}` : `${hand.currentBet === 0 ? "Bet" : "Raise to"} ${fmt(raiseTo)}`}
            </button>
          </div>
        </div>
      ) : (
        <div className="moves">
          <button className="btn danger" disabled={busy} onClick={() => move({ move: "fold" })}>
            Fold
          </button>
          {owe === 0 ? (
            <button className="btn" disabled={busy} onClick={() => move({ move: "check" })}>
              Check
            </button>
          ) : (
            <button className="btn" disabled={busy} onClick={() => move(owe >= actor.chips ? { move: "allin" } : { move: "call" })}>
              {owe >= actor.chips ? `Call all in ${fmt(actor.chips)}` : `Call ${fmt(owe)}`}
            </button>
          )}
          {canRaise && owe < actor.chips && (
            <button className="btn primary" disabled={busy} onClick={() => setEditing(true)}>
              {hand.currentBet === 0 ? "Bet" : "Raise"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function Showdown({ room, hand, busy, onAward }: { room: Room; hand: Hand; busy: boolean; onAward: (w: string[][]) => void }) {
  const list = useMemo(() => pots(hand), [hand]);
  const [picks, setPicks] = useState<string[][]>(() => list.map(() => []));
  useEffect(() => setPicks(list.map(() => [])), [list.length, hand.number]);
  const name = (id: string) => room.players.find((p) => p.id === id)?.name ?? "?";
  const toggle = (i: number, id: string) =>
    setPicks((prev) => prev.map((ws, j) => (j !== i ? ws : ws.includes(id) ? ws.filter((x) => x !== id) : [...ws, id])));
  const ready = picks.length === list.length && picks.every((p) => p.length > 0);

  return (
    <section className="card panel action-panel">
      <h2>Who won?</h2>
      <p className="muted small">Tap the winner of each pot. Tap more than one to split.</p>
      {list.map((pot, i) => (
        <div key={i} className="pot-pick">
          <div className="pot-label">
            {list.length > 1 ? (i === 0 ? "Main pot" : `Side pot ${i}`) : "Pot"} <b>{fmt(pot.amount)}</b>
          </div>
          <div className="presets">
            {pot.eligible.map((id) => (
              <button key={id} className={`chip-btn ${picks[i]?.includes(id) ? "sel" : ""}`} onClick={() => toggle(i, id)} disabled={busy}>
                {name(id)}
              </button>
            ))}
          </div>
        </div>
      ))}
      <button className="btn primary big" disabled={busy || !ready} onClick={() => onAward(picks)}>
        Pay out
      </button>
    </section>
  );
}

function Scoreboard({ room, you, busy, send }: { room: Room; you: string | null; busy: boolean; send: (b: Body) => Promise<boolean> }) {
  const me = room.players.find((p) => p.id === you);
  const ranked = [...room.players].sort((a, b) => b.chips - b.boughtIn - (a.chips - a.boughtIn));
  return (
    <section className="card panel">
      <h3>Scoreboard</h3>
      <table className="score">
        <thead>
          <tr>
            <th>#</th>
            <th>Player</th>
            <th className="num">Chips</th>
            <th className="num">Net</th>
            <th className="num" title="Hands won">Won</th>
          </tr>
        </thead>
        <tbody>
          {ranked.map((p, i) => {
            const net = p.chips - p.boughtIn;
            return (
              <tr key={p.id} className={p.id === you ? "me" : ""}>
                <td className="muted">{i + 1}</td>
                <td>
                  {p.name}
                  {p.boughtIn > room.settings.startingChips && <span className="muted small"> ×{Math.round(p.boughtIn / room.settings.startingChips)}</span>}
                </td>
                <td className="num">{fmt(p.chips)}</td>
                <td className={`num ${net > 0 ? "up" : net < 0 ? "down" : "muted"}`}>
                  {net > 0 ? "+" : ""}
                  {fmt(net)}
                </td>
                <td className="num">{p.handsWon}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {me && <SelfControls room={room} me={me} busy={busy} send={send} />}
    </section>
  );
}

function SelfControls({ room, me, busy, send }: { room: Room; me: Player; busy: boolean; send: (b: Body) => Promise<boolean> }) {
  const inHand = !!room.hand?.order.includes(me.id);
  return (
    <div className="self-controls">
      <button className="btn ghost small-btn" disabled={busy || inHand} onClick={() => send({ kind: "rebuy" })} title={inHand ? "After this hand" : undefined}>
        Rebuy +{fmt(room.settings.startingChips)}
      </button>
      <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "sitout", sittingOut: !me.sittingOut })}>
        {me.sittingOut ? "I'm back" : "Sit out next hands"}
      </button>
    </div>
  );
}

function HostTools({ room, busy, send }: { room: Room; busy: boolean; send: (b: Body) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [sb, setSb] = useState(room.settings.smallBlind);
  const [bb, setBb] = useState(room.settings.bigBlind);
  const [target, setTarget] = useState(room.players[0]?.id ?? "");
  const [stack, setStack] = useState<number | "">("");
  const targetPlayer = room.players.find((p) => p.id === target);

  return (
    <section className="card panel">
      <button className="disclosure" onClick={() => setOpen(!open)} aria-expanded={open}>
        <h3>Host tools</h3>
        <span className={`chev ${open ? "open" : ""}`}>›</span>
      </button>
      {open && (
        <div className="host-body">
          <div className="tool">
            <span className="tool-label">Blinds</span>
            <div className="inline">
              <input type="number" inputMode="numeric" value={sb} onChange={(e) => setSb(Number(e.target.value))} aria-label="Small blind" />
              <span className="muted">/</span>
              <input type="number" inputMode="numeric" value={bb} onChange={(e) => setBb(Number(e.target.value))} aria-label="Big blind" />
              <button className="btn ghost" disabled={busy} onClick={() => send({ kind: "blinds", smallBlind: sb, bigBlind: bb })}>
                Save
              </button>
            </div>
          </div>
          <div className="tool">
            <span className="tool-label">Player</span>
            <div className="inline">
              <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Player">
                {room.players.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <input
                type="number"
                inputMode="numeric"
                placeholder={targetPlayer ? String(targetPlayer.chips) : "chips"}
                value={stack}
                onChange={(e) => setStack(e.target.value === "" ? "" : Number(e.target.value))}
                aria-label="Set stack"
              />
              <button className="btn ghost" disabled={busy || stack === ""} onClick={() => send({ kind: "adjust", playerId: target, chips: stack }).then((ok) => ok && setStack(""))}>
                Set stack
              </button>
            </div>
            <div className="inline">
              <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "rebuy", playerId: target })}>
                Rebuy for {targetPlayer?.name}
              </button>
              <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "sitout", playerId: target, sittingOut: !targetPlayer?.sittingOut })}>
                {targetPlayer?.sittingOut ? "Sit in" : "Sit out"}
              </button>
              {target !== room.hostId && (
                <button
                  className="btn ghost small-btn danger-text"
                  disabled={busy}
                  onClick={() => confirm(`Remove ${targetPlayer?.name} from the table?`) && send({ kind: "remove", playerId: target })}
                >
                  Remove
                </button>
              )}
            </div>
          </div>
          {room.hand && (
            <button className="btn ghost danger-text" disabled={busy} onClick={() => confirm("Cancel this hand and return every bet?") && send({ kind: "cancel" })}>
              Cancel hand &amp; refund bets
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function Log({ room }: { room: Room }) {
  const entries = [...room.log].reverse().slice(0, 25);
  return (
    <section className="card panel">
      <h3>Table talk</h3>
      <ul className="log">
        {entries.map((e, i) => (
          <li key={`${e.at}-${i}`} className={e.text.startsWith("—") || e.text.startsWith("Hand #") ? "sep" : ""}>
            <span>{e.text}</span>
            <time className="muted">{new Date(e.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>
          </li>
        ))}
      </ul>
    </section>
  );
}
