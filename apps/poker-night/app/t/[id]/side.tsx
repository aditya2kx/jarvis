"use client";

import { useState } from "react";
import { fmt } from "@/lib/client";
import type { Player, Room } from "@/lib/game";
import type { Send } from "./dock";
import HostTools from "./host";
import PlayingCard from "@/components/playing-card";

type Tab = "score" | "log" | "learn" | "host";

export default function Side({ room, you, isHost, busy, send }: { room: Room; you: string | null; isHost: boolean; busy: boolean; send: Send }) {
  const [tab, setTab] = useState<Tab>("score");
  const tabs: [Tab, string][] = [
    ["score", "Leaderboard"],
    ["log", "Table talk"],
    ["learn", "How to play"],
    ...(isHost ? ([["host", "Host"]] as [Tab, string][]) : []),
  ];
  return (
    <section className="card side">
      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <div className="tab-body">
        {tab === "score" && <Leaderboard room={room} you={you} busy={busy} send={send} />}
        {tab === "log" && <Log room={room} />}
        {tab === "learn" && <HowToPlay />}
        {tab === "host" && isHost && <HostTools room={room} busy={busy} send={send} />}
      </div>
    </section>
  );
}

type Award = { icon: string; label: string };

function awards(room: Room): Map<string, Award[]> {
  const out = new Map<string, Award[]>();
  const add = (p: Player | undefined, a: Award) => p && out.set(p.id, [...(out.get(p.id) ?? []), a]);
  const by = (f: (p: Player) => number) => {
    const best = [...room.players].sort((a, b) => f(b) - f(a))[0];
    return best && f(best) > 0 && room.players.filter((p) => f(p) === f(best)).length === 1 ? best : undefined;
  };
  if (room.handCount > 0) add(by((p) => p.chips), { icon: "👑", label: "Chip leader" });
  add(by((p) => p.stats.biggestPot), { icon: "💰", label: "Biggest pot" });
  add(
    room.players.find((p) => p.stats.streak >= 2),
    { icon: "🔥", label: "On fire" },
  );
  add(by((p) => p.stats.allIns), { icon: "🎰", label: "Most all-ins" });
  add(by((p) => p.stats.bestHandScore + 1), { icon: "🃏", label: "Best hand" });
  return out;
}

function Sparkline({ room, id }: { room: Room; id: string }) {
  const pts = [room.settings.startingChips, ...room.history.map((h) => h.stacks[id]).filter((v): v is number => v !== undefined)];
  if (pts.length < 2) return <svg className="spark" viewBox="0 0 60 18" aria-hidden />;
  const max = Math.max(...pts);
  const min = Math.min(...pts);
  const span = max - min || 1;
  const d = pts.map((v, i) => `${(i / (pts.length - 1)) * 60},${16 - ((v - min) / span) * 14}`).join(" ");
  const up = pts[pts.length - 1] >= pts[0];
  return (
    <svg className="spark" viewBox="0 0 60 18" aria-hidden>
      <polyline points={d} fill="none" stroke={up ? "#6fd39a" : "#e0675b"} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Leaderboard({ room, you, busy, send }: { room: Room; you: string | null; busy: boolean; send: Send }) {
  const [open, setOpen] = useState<string | null>(null);
  const ranked = [...room.players].sort((a, b) => b.chips - b.boughtIn - (a.chips - a.boughtIn));
  const badges = awards(room);
  const me = room.players.find((p) => p.id === you);
  const inHand = !!me && !!room.hand?.order.includes(me.id);
  return (
    <div>
      <ul className="board-list">
        {ranked.map((p, i) => {
          const net = p.chips - p.boughtIn;
          const s = p.stats;
          return (
            <li key={p.id} className={p.id === you ? "me" : ""}>
              <button className="lb-row" onClick={() => setOpen(open === p.id ? null : p.id)} aria-expanded={open === p.id}>
                <span className={`rank r${i + 1}`}>{i + 1}</span>
                <span className="lb-av">{p.avatar}</span>
                <span className="lb-name">
                  {p.name}
                  <span className="lb-badges">
                    {(badges.get(p.id) ?? []).map((b) => (
                      <span key={b.label} title={b.label}>
                        {b.icon}
                      </span>
                    ))}
                  </span>
                </span>
                <Sparkline room={room} id={p.id} />
                <span className="lb-num">
                  <b>{fmt(p.chips)}</b>
                  <small className={net > 0 ? "up" : net < 0 ? "down" : "muted"}>
                    {net > 0 ? "+" : ""}
                    {fmt(net)}
                  </small>
                </span>
              </button>
              {open === p.id && (
                <div className="lb-stats">
                  <Stat label="Hands won" value={`${p.handsWon} / ${s.handsPlayed}`} />
                  <Stat label="Plays (VPIP)" value={s.handsPlayed ? `${Math.round((s.vpip / s.handsPlayed) * 100)}%` : "—"} />
                  <Stat label="Biggest pot" value={s.biggestPot ? fmt(s.biggestPot) : "—"} />
                  <Stat label="Best streak" value={s.bestStreak ? `${s.bestStreak} in a row` : "—"} />
                  {s.allIns > 0 && <Stat label="All-ins" value={String(s.allIns)} />}
                  <Stat label="Best hand" value={s.bestHand ?? "—"} />
                  <Stat label="Bought in" value={fmt(p.boughtIn)} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {me && (
        <div className="self-controls">
          <button className="btn ghost small-btn" disabled={busy || inHand} onClick={() => send({ kind: "rebuy" })} title={inHand ? "After this hand" : undefined}>
            💸 Rebuy +{fmt(room.settings.startingChips)}
          </button>
          <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "sitout", sittingOut: !me.sittingOut })}>
            {me.sittingOut ? "🙋 I'm back" : "☕ Sit out"}
          </button>
        </div>
      )}
      <p className="muted tiny">Tap a player for stats. VPIP = how often they put chips in pre-flop by choice — high means loose, low means tight.</p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function Log({ room }: { room: Room }) {
  const entries = [...room.log].reverse().slice(0, 40);
  return (
    <ul className="log">
      {entries.map((e, i) => (
        <li key={`${e.at}-${i}`} className={e.text.startsWith("🃏") ? "sep" : e.text.startsWith("🏆") ? "win" : ""}>
          <span>{e.text}</span>
          <time className="muted">{new Date(e.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>
        </li>
      ))}
    </ul>
  );
}

export const RANKINGS: [string, string[], string][] = [
  ["Royal flush", ["As", "Ks", "Qs", "Js", "Ts"], "A-K-Q-J-10, same suit"],
  ["Straight flush", ["9h", "8h", "7h", "6h", "5h"], "Five in a row, same suit"],
  ["Four of a kind", ["Qc", "Qd", "Qh", "Qs", "4d"], "Four of the same rank"],
  ["Full house", ["Kc", "Kd", "Kh", "7s", "7d"], "Three of a kind + a pair"],
  ["Flush", ["Ad", "Jd", "8d", "5d", "2d"], "Any five of the same suit"],
  ["Straight", ["Tc", "9d", "8h", "7s", "6c"], "Five in a row, any suits"],
  ["Three of a kind", ["8c", "8d", "8h", "Ks", "3d"], "Three of the same rank"],
  ["Two pair", ["Ac", "Ad", "5h", "5s", "Jd"], "Two different pairs"],
  ["Pair", ["Jc", "Jd", "9h", "5s", "2d"], "Two of the same rank"],
  ["High card", ["Ac", "Qd", "8h", "5s", "3d"], "Nothing else — highest card plays"],
];

export function HandRankings() {
  return (
    <ol className="rankings">
      {RANKINGS.map(([name, cards, desc], i) => (
        <li key={name}>
          <span className="rk-n">{i + 1}</span>
          <div className="rk-text">
            <b>{name}</b>
            <small className="muted">{desc}</small>
          </div>
          <div className="rk-cards">
            {cards.map((c) => (
              <PlayingCard key={c} card={c} size="xs" />
            ))}
          </div>
        </li>
      ))}
    </ol>
  );
}

function HowToPlay() {
  return (
    <div className="learn">
      <h4>A hand in 60 seconds</h4>
      <ol className="steps">
        <li>
          <b>Blinds.</b> The two players left of the dealer post forced bets so there&apos;s something to win.
        </li>
        <li>
          <b>Two private cards each.</b> Betting goes around: fold, call, or raise.
        </li>
        <li>
          <b>Flop, turn, river.</b> Five shared cards come out (3, then 1, then 1) with betting after each.
        </li>
        <li>
          <b>Showdown.</b> Best five-card hand using any of your 2 + the 5 shared wins the pot.
        </li>
      </ol>
      <h4>Hand rankings (best first)</h4>
      <HandRankings />
      <h4>Words you&apos;ll hear</h4>
      <dl className="glossary">
        <dt>Button</dt>
        <dd>The dealer seat. It moves one seat left every hand and acts last after the flop — the best seat.</dd>
        <dt>Pot odds</dt>
        <dd>Call ÷ (pot + call). If your chance to win beats that number, calling makes money over time.</dd>
        <dt>Side pot</dt>
        <dd>When someone is all in, extra bets go to a separate pot they can&apos;t win.</dd>
        <dt>Kicker</dt>
        <dd>Your highest unpaired card — breaks ties between equal hands.</dd>
      </dl>
    </div>
  );
}
