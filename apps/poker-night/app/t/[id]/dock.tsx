"use client";

import { useEffect, useMemo, useState } from "react";
import { describeStart, evaluate, winChance } from "@/lib/cards";
import { fmt } from "@/lib/client";
import { MOVE_HELP, pct, potOddsNeeded, verdict } from "@/lib/coach";
import { nextDealerId, pots, potTotal, REACTIONS, type Hand, type Room } from "@/lib/game";
import PlayingCard from "@/components/playing-card";

export type Send = (body: Record<string, unknown>) => Promise<boolean>;

type Props = {
  room: Room;
  you: string;
  isHost: boolean;
  busy: boolean;
  coach: boolean;
  lastCards: string[] | null;
  send: Send;
};

export default function Dock(props: Props) {
  const { room, you, busy, send } = props;
  const h = room.hand;
  return (
    <section className="dock">
      {h ? (
        h.street === "showdown" ? (
          <Showdown room={room} hand={h} busy={busy} send={send} />
        ) : (
          <InHand {...props} hand={h} />
        )
      ) : (
        <BetweenHands {...props} />
      )}
      <div className="reactions">
        {REACTIONS.map((e) => (
          <button key={e} className="react-btn" onClick={() => send({ kind: "react", emoji: e })} aria-label={`React ${e}`}>
            {e}
          </button>
        ))}
        {room.players.find((p) => p.id === you)?.sittingOut && <span className="muted small away-note">You&apos;re sitting out</span>}
      </div>
    </section>
  );
}

function InHand({ room, hand, you, isHost, busy, coach, send }: Props & { hand: Hand }) {
  const actor = room.players.find((p) => p.id === hand.toAct);
  const meInHand = hand.order.includes(you) && !hand.folded.includes(you);
  const myCards = hand.holes?.[you];
  const board = hand.board ?? [];
  const opponents = hand.order.filter((id) => id !== you && !hand.folded.includes(id)).length;
  const equity = useMemo(
    () => (coach && myCards && meInHand && opponents > 0 ? winChance(myCards, board, opponents, 900) : null),
    [coach, myCards?.join(), board.join(), opponents, meInHand],
  );
  const handName = myCards ? (board.length ? evaluate([...myCards, ...board]).name : describeStart(myCards)) : null;
  const [proxy, setProxy] = useState(false);
  const mine = actor?.id === you;
  useEffect(() => setProxy(false), [actor?.id]);

  return (
    <>
      {myCards && (
        <div className={`my-hand ${meInHand ? "" : "folded"}`}>
          <div className="my-cards">
            {myCards.map((c, i) => (
              <PlayingCard key={c} card={c} size="lg" delay={i * 120} />
            ))}
          </div>
          <div className="my-info">
            <div className="my-label">{meInHand ? "Your hand" : "You folded"}</div>
            <div className="my-name">{handName}</div>
            {coach && equity !== null && (
              <div className="meter" title="Estimated chance to win if everyone stays to the end">
                <div className="meter-bar">
                  <i style={{ width: pct(equity) }} className={equity > 0.5 ? "good" : equity < 0.25 ? "bad" : ""} />
                </div>
                <span>{pct(equity)} to win</span>
              </div>
            )}
          </div>
        </div>
      )}
      {!actor ? null : mine || (isHost && proxy) ? (
        <Actions room={room} hand={hand} actorId={actor.id} you={you} coach={coach} equity={mine ? equity : null} busy={busy} send={send} onDone={() => setProxy(false)} />
      ) : (
        <div className="waiting2">
          <span className="pulse" />
          <span>
            {actor.avatar} <b>{actor.name}</b> is thinking
            {hand.currentBet - (hand.bets[actor.id] ?? 0) > 0 ? ` · ${fmt(hand.currentBet - (hand.bets[actor.id] ?? 0))} to call` : ""}
          </span>
          {isHost && (
            <button className="btn ghost small-btn" onClick={() => setProxy(true)}>
              Act for {actor.name}
            </button>
          )}
        </div>
      )}
    </>
  );
}

function Actions({ room, hand, actorId, you, coach, equity, busy, send, onDone }: { room: Room; hand: Hand; actorId: string; you: string; coach: boolean; equity: number | null; busy: boolean; send: Send; onDone: () => void }) {
  const actor = room.players.find((p) => p.id === actorId)!;
  const mine = actorId === you;
  const myBet = hand.bets[actorId] ?? 0;
  const owe = Math.max(0, hand.currentBet - myBet);
  const maxTo = myBet + actor.chips;
  const minTo = Math.min(maxTo, hand.currentBet + hand.minRaise);
  const pot = potTotal(hand);
  const [raiseTo, setRaiseTo] = useState(minTo);
  const [raising, setRaising] = useState(false);
  useEffect(() => {
    setRaiseTo(minTo);
    setRaising(false);
  }, [actorId, hand.currentBet, minTo, hand.street]);

  const move = (m: Record<string, unknown>) => send({ kind: "act", playerId: actorId, ...m }).then((ok) => ok && setRaising(false));
  const canRaise = maxTo > hand.currentBet && owe < actor.chips;
  const clamp = (n: number) => Math.max(minTo, Math.min(maxTo, Math.round(n / room.settings.smallBlind) * room.settings.smallBlind));
  const presets: [string, number][] = [
    ["Min", minTo],
    ["½ pot", clamp(hand.currentBet + (pot + owe) / 2)],
    ["¾ pot", clamp(hand.currentBet + ((pot + owe) * 3) / 4)],
    ["Pot", clamp(hand.currentBet + pot + owe)],
    ["All in", maxTo],
  ];
  const advice = coach && mine ? verdict(owe, pot, equity) : null;
  const betWord = hand.currentBet === 0 ? "Bet" : "Raise";

  return (
    <div className={`actions ${mine ? "your-turn" : ""}`}>
      <div className="act-head">
        <span className="act-title">{mine ? "Your move" : `Acting for ${actor.name}`}</span>
        <span className="muted small">
          {owe > 0 ? `${fmt(owe)} to call` : "No bet yet"} · stack {fmt(actor.chips)}
          {!coach && owe > 0 && <> · odds {pct(potOddsNeeded(owe, pot))}</>}
        </span>
        {!mine && (
          <button className="btn ghost small-btn" onClick={onDone}>
            Done
          </button>
        )}
      </div>
      {advice && <div className={`coach ${advice.tone}`}>🎓 {advice.text}</div>}

      {raising ? (
        <div className="raise2">
          <div className="presets">
            {presets.map(([label, v]) => (
              <button key={label} className={`chip-btn ${raiseTo === v ? "sel" : ""}`} onClick={() => setRaiseTo(v)} disabled={busy}>
                {label}
              </button>
            ))}
          </div>
          <div className="raise-amt">
            <button className="btn ghost round" onClick={() => setRaiseTo(clamp(raiseTo - room.settings.bigBlind))} aria-label="Less">
              −
            </button>
            <input type="range" className="slider" min={minTo} max={maxTo} step={room.settings.smallBlind} value={raiseTo} onChange={(e) => setRaiseTo(Number(e.target.value))} aria-label="Raise amount" />
            <button className="btn ghost round" onClick={() => setRaiseTo(clamp(raiseTo + room.settings.bigBlind))} aria-label="More">
              +
            </button>
          </div>
          <div className="moves2">
            <button className="btn ghost" onClick={() => setRaising(false)} disabled={busy}>
              Back
            </button>
            <button className="btn primary wide" disabled={busy} onClick={() => move(raiseTo >= maxTo ? { move: "allin" } : { move: "raise", to: raiseTo })}>
              {raiseTo >= maxTo ? `All in · ${fmt(maxTo)}` : `${betWord} to ${fmt(raiseTo)}`}
            </button>
          </div>
        </div>
      ) : (
        <div className="moves2">
          <MoveButton label="Fold" help={coach ? MOVE_HELP.fold : undefined} className="danger" disabled={busy} onClick={() => move({ move: "fold" })} />
          {owe === 0 ? (
            <MoveButton label="Check" help={coach ? MOVE_HELP.check : undefined} disabled={busy} onClick={() => move({ move: "check" })} />
          ) : (
            <MoveButton
              label={owe >= actor.chips ? `All in ${fmt(actor.chips)}` : `Call ${fmt(owe)}`}
              help={coach ? MOVE_HELP.call : undefined}
              disabled={busy}
              onClick={() => move(owe >= actor.chips ? { move: "allin" } : { move: "call" })}
            />
          )}
          {canRaise && <MoveButton label={`${betWord}…`} help={coach ? MOVE_HELP.raise : undefined} className="primary" disabled={busy} onClick={() => setRaising(true)} />}
        </div>
      )}
    </div>
  );
}

function MoveButton({ label, help, className = "", disabled, onClick }: { label: string; help?: string; className?: string; disabled: boolean; onClick: () => void }) {
  return (
    <button className={`btn move ${className}`} disabled={disabled} onClick={onClick}>
      <span>{label}</span>
      {help && <small>{help}</small>}
    </button>
  );
}

function Showdown({ room, hand, busy, send }: { room: Room; hand: Hand; busy: boolean; send: Send }) {
  const list = useMemo(() => pots(hand), [hand]);
  const [picks, setPicks] = useState<string[][]>(() => list.map(() => []));
  useEffect(() => setPicks(list.map(() => [])), [list.length, hand.number]);
  const player = (id: string) => room.players.find((p) => p.id === id);
  const toggle = (i: number, id: string) => setPicks((prev) => prev.map((ws, j) => (j !== i ? ws : ws.includes(id) ? ws.filter((x) => x !== id) : [...ws, id])));
  const ready = picks.length === list.length && picks.every((p) => p.length > 0);

  return (
    <div className="actions">
      <div className="act-head">
        <span className="act-title">Who won? 🏆</span>
        <span className="muted small">Tap the best hand. Tap two to split.</span>
      </div>
      {list.map((pot, i) => (
        <div key={i} className="pot-pick">
          <div className="pot-label">
            {list.length > 1 ? (i === 0 ? "Main pot" : `Side pot ${i}`) : "Pot"} <b>{fmt(pot.amount)}</b>
          </div>
          <div className="presets">
            {pot.eligible.map((id) => (
              <button key={id} className={`chip-btn ${picks[i]?.includes(id) ? "sel" : ""}`} onClick={() => toggle(i, id)} disabled={busy}>
                {player(id)?.avatar} {player(id)?.name}
              </button>
            ))}
          </div>
        </div>
      ))}
      <button className="btn primary big" disabled={busy || !ready} onClick={() => send({ kind: "showdown", winners: picks })}>
        Pay out
      </button>
    </div>
  );
}

function BetweenHands({ room, you, busy, lastCards, send }: Props) {
  const ready = room.players.filter((p) => !p.sittingOut && p.chips > 0);
  const dealer = room.players.find((p) => p.id === nextDealerId(room));
  const me = room.players.find((p) => p.id === you)!;
  const r = room.lastResult;
  const canShow = !!r && !!lastCards && room.settings.dealMode === "app" && !r.shown.some((s) => s.id === you);
  return (
    <div className="actions">
      {ready.length < 2 ? (
        <p className="muted center-text">Waiting for at least two players with chips — tap “Invite” to share the link.</p>
      ) : (
        <>
          <p className="muted center-text">
            {dealer?.avatar} <b className="cream">{dealer?.name}</b> has the button next · blinds {fmt(room.settings.smallBlind)}/{fmt(room.settings.bigBlind)} post automatically
          </p>
          <div className="moves2">
            {canShow && (
              <button className="btn ghost" disabled={busy} onClick={() => send({ kind: "show" })}>
                👀 Show my cards
              </button>
            )}
            <button className="btn primary big deal-btn" disabled={busy} onClick={() => send({ kind: "start" })}>
              {room.settings.dealMode === "app" ? "Shuffle & deal" : "Start next hand"}
            </button>
          </div>
        </>
      )}
      {me.chips === 0 && (
        <button className="btn ghost" disabled={busy} onClick={() => send({ kind: "rebuy" })}>
          💸 Rebuy {fmt(room.settings.startingChips)}
        </button>
      )}
    </div>
  );
}
