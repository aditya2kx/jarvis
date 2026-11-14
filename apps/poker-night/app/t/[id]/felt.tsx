"use client";

import { useMemo } from "react";
import { fmt } from "@/lib/client";
import { BOARD_CARDS, pots, potTotal, type Hand, type Player, type Room, type Street } from "@/lib/game";
import PlayingCard from "@/components/playing-card";

export const SHOT_CLOCK_MS = 30_000;

const STREET_LABEL: Record<Street, string> = {
  preflop: "Pre-flop",
  flop: "Flop",
  turn: "Turn",
  river: "River",
  showdown: "Showdown",
};

function dealerHint(h: Hand, physical: boolean) {
  if (!physical) return null;
  switch (h.street) {
    case "preflop":
      return "Dealer: two cards to each player";
    case "flop":
      return "Dealer: burn one, flip three";
    case "turn":
      return "Dealer: burn one, flip the turn";
    case "river":
      return "Dealer: burn one, flip the river";
    case "showdown":
      return "Flip your cards — then tap who won";
  }
}

type Props = { room: Room; you: string | null; now: number };

export default function Felt({ room, you, now }: Props) {
  const h = room.hand;
  const physical = room.settings.dealMode === "physical";
  const result = !h ? room.lastResult : null;
  const fresh = !!result && now - result.at < 6000;

  const seats = useMemo(() => {
    const idx = room.players.findIndex((p) => p.id === you);
    return idx > 0 ? [...room.players.slice(idx), ...room.players.slice(0, idx)] : room.players;
  }, [room.players, you]);

  const n = seats.length;
  return (
    <section className={`felt2 ${n > 6 ? "crowded" : ""}`}>
      <div className="rail">
        <div className="cloth">
          <div className="cloth-logo" aria-hidden>
            ♠ POKER NIGHT ♥
          </div>
          <Center room={room} physical={physical} fresh={fresh} />
        </div>
      </div>
      {seats.map((p, i) => {
        const angle = Math.PI / 2 + (i * 2 * Math.PI) / n;
        return <Seat key={p.id} room={room} p={p} you={you} now={now} angle={angle} />;
      })}
      {fresh && <Confetti seed={result!.at} />}
    </section>
  );
}

function Center({ room, physical, fresh }: { room: Room; physical: boolean; fresh: boolean }) {
  const h = room.hand;
  const result = room.lastResult;
  if (h) {
    const dealt = BOARD_CARDS[h.street];
    const total = potTotal(h);
    // Only contested pots count as side pots — SB/BB alone don't create a "side pot" to show.
    const contestedPots = pots(h).filter((p) => p.eligible.length >= 2).length;
    return (
      <div className="center">
        <div className="street-pill">
          Hand {h.number} · {STREET_LABEL[h.street]}
        </div>
        <div className="board2">
          {Array.from({ length: 5 }, (_, i) => {
            const card = h.board?.[i];
            if (card) return <PlayingCard key={card} card={card} size="md" delay={(i % 3) * 120} />;
            if (physical && i < dealt) return <div key={i} className="pc pc-md pc-physical" aria-label="Real card on the table" />;
            return <PlayingCard key={`s${i}`} slot size="md" />;
          })}
        </div>
        <div className="pot2">
          <ChipStack amount={total} />
          <span className="pot-amt">{fmt(total)}</span>
          {contestedPots > 1 && <span className="side-note">{contestedPots - 1} side pot{contestedPots > 2 ? "s" : ""}</span>}
        </div>
        {dealerHint(h, physical) && <div className="hint2">{dealerHint(h, physical)}</div>}
      </div>
    );
  }
  if (result) {
    const names = result.winners.map((w) => room.players.find((p) => p.id === w.id)?.name ?? "?");
    const amount = result.winners.reduce((a, w) => a + w.amount, 0);
    const handName = result.winners[0]?.handName;
    return (
      <div className={`center result2 ${fresh ? "pop" : ""}`}>
        {result.board.length > 0 && (
          <div className="board2 dim-board">
            {result.board.map((c) => (
              <PlayingCard key={c} card={c} size="sm" />
            ))}
          </div>
        )}
        <div className="trophy">🏆</div>
        <div className="win-title">
          {names.join(" & ")} {names.length > 1 ? "split" : "wins"} {fmt(amount)}
        </div>
        <div className="win-sub">{result.uncontested ? "Everyone else folded" : (handName ?? "Pot awarded")}</div>
      </div>
    );
  }
  return (
    <div className="center">
      <div className="suits-row" aria-hidden>
        <span>♠</span>
        <span className="red">♥</span>
        <span>♣</span>
        <span className="red">♦</span>
      </div>
      <div className="win-sub">Waiting for the first deal</div>
    </div>
  );
}

export function ChipStack({ amount }: { amount: number }) {
  const count = Math.max(1, Math.min(5, Math.ceil(Math.log10(Math.max(10, amount)))));
  return (
    <span className="chips3" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <i key={i} style={{ bottom: i * 4 }} className={`c${i % 4}`} />
      ))}
    </span>
  );
}

function Seat({ room, p, you, now, angle }: { room: Room; p: Player; you: string | null; now: number; angle: number }) {
  const h = room.hand;
  const result = !h ? room.lastResult : null;
  const inHand = !!h?.order.includes(p.id);
  const folded = !!h?.folded.includes(p.id);
  const turn = h?.toAct === p.id;
  const bet = h?.bets[p.id] ?? 0;
  const won = result?.winners.find((w) => w.id === p.id);
  const shown = result?.shown.find((s) => s.id === p.id);
  const elapsed = turn && h ? now - h.turnStartedAt : 0;
  const left = 1 - Math.min(1, elapsed / SHOT_CLOCK_MS);
  const late = turn && elapsed > SHOT_CLOCK_MS;

  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const pos = { left: `${50 + 45 * cos}%`, top: `${50 + 43 * sin}%` };
  const betPos = { left: `${50 + 27 * cos}%`, top: `${50 + 24 * sin}%` };
  const btnPos = { left: `${50 + 33 * Math.cos(angle - 0.32)}%`, top: `${50 + 31 * Math.sin(angle - 0.32)}%` };

  const status = !h
    ? p.sittingOut
      ? "Away"
      : p.chips === 0
        ? "Busted"
        : null
    : !inHand
      ? "Next hand"
      : folded
        ? "Fold"
        : p.chips === 0
          ? "All in"
          : null;

  const myCards = h?.holes?.[p.id];
  const showBacks = inHand && !folded && room.settings.dealMode === "app";
  const reactions = room.reactions.filter((r) => r.playerId === p.id && now - r.at < 4500);

  return (
    <>
      <div className={`seat2 ${turn ? "turn" : ""} ${late ? "late" : ""} ${folded || (h && !inHand) || p.sittingOut ? "dim" : ""} ${p.id === you ? "me" : ""} ${won ? "won" : ""}`} style={pos}>
        <div className="seat-cards">
          {shown ? (
            shown.cards.map((c) => <PlayingCard key={c} card={c} size="xs" />)
          ) : showBacks ? (
            myCards ? (
              myCards.map((c) => <PlayingCard key={c} card={c} size="xs" />)
            ) : (
              <>
                <PlayingCard faceDown size="xs" />
                <PlayingCard faceDown size="xs" delay={80} />
              </>
            )
          ) : null}
        </div>
        <div className="avatar-wrap">
          {turn && (
            <svg className="ring" viewBox="0 0 48 48" aria-hidden>
              <circle cx="24" cy="24" r="22" pathLength="100" strokeDasharray="100" strokeDashoffset={100 - left * 100} />
            </svg>
          )}
          <div className="avatar">{p.avatar}</div>
          {reactions.map((r) => (
            <span key={r.id} className="float-emoji">
              {r.emoji}
            </span>
          ))}
          {won && <span className="float-win">+{fmt(won.amount)}</span>}
        </div>
        <div className="plate">
          <div className="nm">{p.name}</div>
          <div className="stk">{fmt(p.chips)}</div>
        </div>
        {status && <div className={`tag ${status === "All in" ? "hot" : ""}`}>{status}</div>}
        {shown && <div className="tag show">{shown.handName.split(",")[0]}</div>}
        {late && <div className="tag hot">⏰</div>}
      </div>
      {bet > 0 && (
        <div className="bet2" style={betPos}>
          <ChipStack amount={bet} />
          {fmt(bet)}
        </div>
      )}
      {h?.dealerId === p.id && (
        <div className="dbtn" style={btnPos} title="Dealer button">
          D
        </div>
      )}
    </>
  );
}

function Confetti({ seed }: { seed: number }) {
  const pieces = useMemo(() => {
    let x = seed % 9973;
    const rand = () => ((x = (x * 9301 + 49297) % 233280) / 233280);
    return Array.from({ length: 36 }, (_, i) => ({
      left: rand() * 100,
      delay: rand() * 0.6,
      dur: 1.8 + rand() * 1.4,
      rot: rand() * 360,
      color: ["#e2b65c", "#f3ead6", "#e0675b", "#6fd39a", "#7cc4ff"][i % 5],
    }));
  }, [seed]);
  return (
    <div className="confetti" aria-hidden>
      {pieces.map((c, i) => (
        <i key={i} style={{ left: `${c.left}%`, animationDelay: `${c.delay}s`, animationDuration: `${c.dur}s`, background: c.color, transform: `rotate(${c.rot}deg)` }} />
      ))}
    </div>
  );
}
